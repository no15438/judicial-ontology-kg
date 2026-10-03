"""Adapt fixed AppealCase releases without concatenating or rewriting source fields."""

import hashlib
import json
import re
import unicodedata
from collections import Counter, defaultdict
from pathlib import Path

from judicial_kg.sources.records import canonical_hash, document, evidence_span, pointer_get

BODY_SHA256 = "eb98f0ef85d36fde0304dbeff1e6f10c33741d945933071827bcb0b790130c67"
META_SHA256 = "b92627bb62243312c374fe3ce087ed44bd4b2ef1143b51780f020c25e22b04a7"
VERSION = "9a62b2c3e8bb18c7c5a264c4df40b5578b18c990"
SIDES = ("first-instance", "second-instance")
FIELDS = ("header", "claim", "fact_description", "court_view", "judgment", "footer")
DOCKET = re.compile(r"\(\d{4}\)[\u4e00-\u9fffA-Za-z0-9]{1,40}号")


def normalized_docket(text: str) -> str:
    return "".join(unicodedata.normalize("NFKC", text).split())


def load_verified(path: Path, expected: str):
    raw = path.read_bytes()
    if hashlib.sha256(raw).hexdigest() != expected:
        raise ValueError(f"Pinned source hash mismatch: {path.name}")
    return json.loads(raw)


class Groups:
    def __init__(self, size):
        self.parents = list(range(size))

    def root(self, i):
        while self.parents[i] != i:
            self.parents[i] = self.parents[self.parents[i]]
            i = self.parents[i]
        return i

    def link(self, a, b):
        a, b = self.root(a), self.root(b)
        self.parents[max(a, b)] = min(a, b)


def build_development(body_path: Path, meta_path: Path, exposure_path: Path) -> tuple[list, dict]:
    body = load_verified(body_path, BODY_SHA256)
    meta = load_verified(meta_path, META_SHA256)
    if len(body) != len(meta) or len(body) != 10000:
        raise ValueError("Unexpected pinned release size")
    meta_by_index = {row["index"]: (i, row) for i, row in enumerate(meta)}
    if len(meta_by_index) != len(meta) or len({r["index"] for r in body}) != len(body):
        raise ValueError("Duplicate native record index")
    docs, strict_positions, normalized_texts = [], set(), []
    docket_docs, text_docs = defaultdict(list), defaultdict(list)
    failures = []
    for position, row in enumerate(body):
        meta_position, native_meta = meta_by_index[row["index"]]
        if all(native_meta[side]["cause_of_action"] == "民间借贷纠纷" for side in SIDES):
            strict_positions.add(position)
        for side in SIDES:
            source = {
                "version": VERSION,
                "file": body_path.name,
                "file_sha256": BODY_SHA256,
                "file_hash_verified": True,
                "record_pointer": f"/{position}/{side}",
                "native_index": row["index"],
                "side": side,
                "metadata_file": meta_path.name,
                "metadata_sha256": META_SHA256,
                "metadata_pointer": f"/{meta_position}/{side}",
            }
            sections = row[side]
            if set(sections) != set(FIELDS):
                raise ValueError(f"Unexpected source fields at row {position}/{side}")
            record = document(
                "appealcase",
                source,
                {name: (f"/{position}/{side}/{name}", sections[name]) for name in FIELDS},
                admission="published_source_development_only",
                case_type=native_meta[side]["cause_of_action"],
                annotation_origin="upstream_rules_llm_and_selective_human_review",
                external_court_text_verified=False,
                allocation="unallocated_not_test",
            )
            i = len(docs)
            docket = normalized_docket(native_meta[side]["case"]["no"])
            if not docket or docket not in normalized_docket(sections["header"]):
                failures.append(
                    {"document_id": record["document_id"], "reason": "docket_not_in_header"}
                )
                record["admission"] = "quarantine"
            if docket:
                docket_docs[docket].append(i)
            text_docs[canonical_hash(sections)].append(i)
            normalized_texts.append(normalized_docket("\n".join(sections[f] for f in FIELDS)))
            docs.append(record)
    groups = Groups(len(docs))
    for i in range(0, len(docs), 2):
        groups.link(i, i + 1)
    for members in list(docket_docs.values()) + list(text_docs.values()):
        for member in members[1:]:
            groups.link(members[0], member)
    reference_links = 0
    for i, text in enumerate(normalized_texts):
        for docket in set(DOCKET.findall(text)):
            for target in docket_docs.get(docket, []):
                if target != i:
                    groups.link(i, target)
                    reference_links += 1
    components = defaultdict(list)
    for i in range(len(docs)):
        components[groups.root(i)].append(i)
    exposure = json.loads(exposure_path.read_text())
    if any(
        r["file_sha256"] != BODY_SHA256 or r["allocation"] != "development_exposed"
        for r in exposure["records"]
    ):
        raise ValueError("Exposure entry version or allocation does not match development source")
    inherited = {
        r["record_pointer"]
        for r in exposure["records"]
        if r["file_sha256"] == BODY_SHA256 and r["allocation"] == "development_exposed"
    }
    available = {d["source"]["record_pointer"] for d in docs}
    if not inherited or len(inherited) != len(exposure["records"]) or not inherited <= available:
        raise ValueError("Development exposure must resolve against the verified release")
    exposed_groups = {
        groups.root(i) for i, d in enumerate(docs) if d["source"]["record_pointer"] in inherited
    }
    selected, group_manifest = [], []
    for root, members in components.items():
        group_id = "family:" + canonical_hash(sorted(docs[i]["document_id"] for i in members))
        exposed = root in exposed_groups
        for i in members:
            docs[i]["family_id"] = group_id
            docs[i]["allocation"] = "development_exposed" if exposed else "unallocated_not_test"
            if exposed:
                selected.append(docs[i])
        group_manifest.append(
            {
                "family_id": group_id,
                "document_ids": [docs[i]["document_id"] for i in members],
                "allocation": "development_exposed" if exposed else "unallocated_not_test",
            }
        )
    replayed = empty = 0
    for doc in selected:
        for field in doc["fields"]:
            if pointer_get(body, field["source_pointer"]) != field["text"]:
                raise ValueError("Source pointer replay mismatch")
            if field["text"]:
                span = evidence_span(doc, field["name"], 0, len(field["text"]))
                if span["quote"] != pointer_get(body, span["source_pointer"]):
                    raise ValueError("Field span replay mismatch")
                replayed += 1
            else:
                empty += 1
    report = {
        "schema_version": 1,
        "dataset": "AppealCase",
        "source_version": VERSION,
        "source_files": [
            {"file": body_path.name, "sha256": BODY_SHA256},
            {"file": meta_path.name, "sha256": META_SHA256},
        ],
        "pairs": len(body),
        "documents": len(docs),
        "strict_both_sides_loan_pairs": len(strict_positions),
        "grouping": {
            "families": len(components),
            "repeated_docket_keys": sum(len(x) > 1 for x in docket_docs.values()),
            "repeated_section_hashes": sum(len(x) > 1 for x in text_docs.values()),
            "embedded_known_docket_links": reference_links,
            "rules": [
                "author_pair",
                "normalized_native_docket",
                "exact_section_hash",
                "embedded_known_docket",
            ],
            "caveat": (
                "Conservative docket mentions can over-group; near duplicates and "
                "cross-corpus links remain unchecked"
            ),
        },
        "development": {
            "inherited_documents": len(inherited),
            "propagated_documents": len(selected),
            "families": len(exposed_groups),
            "case_type_counts": dict(Counter(d["case_type"] for d in selected)),
            "source_fields_replayed": replayed,
            "empty_fields_replayed": empty,
            "document_ids": [d["document_id"] for d in selected],
        },
        "failures": failures,
        "failure_documents": len(failures),
        "graph_gold_available": False,
        "semantic_precision_recall_f1": None,
        "external_court_text_verified": False,
        "test_split_frozen": False,
        "group_manifest": group_manifest,
    }
    return selected, report
