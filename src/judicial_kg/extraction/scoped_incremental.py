"""V13: reuse exact grounded Mention echoes, retaining each model occurrence receipt."""

import copy
import re

from judicial_kg.extraction import identity_first, scoped
from judicial_kg.graph.diagnostics import union_ranges
from judicial_kg.sources.records import canonical_hash

CONTRACT_VERSION = scoped.CONTRACT_VERSION
NORMALIZER_VERSION = scoped.NORMALIZER_VERSION
PROMPT_FILE = "prompts/qwen-extraction-v13.txt"
GENERATION_CONSTRAINT_VERSION = "scoped-exact-mention-reuse-v1"
GENERATION_CONSTRAINT_SCOPE = "assertion_only_sources_with_exact_grounded_echo_receipts"
draft_schema = scoped.draft_schema
response_format = scoped.response_format


def merge_body(combined, index, body, save):
    # The runner has validated body source-unit enums, actor IDs and local IDs.
    # Existing selectors were fully grounded before this body was requested.
    result = identity_first.merge_body(combined, index, body, namespace="v13")
    aliases = []
    old_counts = {a["id"]: len(a["mentions"]) for a in combined["actors"]}
    for actor in result["actors"]:
        known = {
            canonical_hash({"kind": m["kind"], "evidence": m["evidence"]}): m["id"]
            for m in actor["mentions"][: old_counts[actor["id"]]]
        }
        kept = []
        for position, mention in enumerate(actor["mentions"]):
            key = canonical_hash({"kind": mention["kind"], "evidence": mention["evidence"]})
            if position >= old_counts[actor["id"]] and key in known:
                aliases.append(
                    {
                        "decision": "reuse_exact_grounded_mention",
                        "actor_id": actor["id"],
                        "model_mention_id": mention["id"],
                        "canonical_mention_id": known[key],
                        "kind": mention["kind"],
                        "selector": copy.deepcopy(mention["evidence"]),
                        "model_mention_sha256": canonical_hash(mention),
                    }
                )
                continue
            kept.append(mention)
        actor["mentions"] = kept
    save(
        f"body-{index:03}-mention-reuse",
        {"context_id": combined["contexts"][index]["id"], "aliases": aliases},
    )
    return result


def header_diagnostics(record, doc, save):
    """Literal reporting cues are review hints, not a legal classification or gate."""
    hints = []
    for context in record["contexts"]:
        if context["kind"] != "court_finding":
            continue
        intervals = {}
        for span in context["assertion_evidence"]:
            intervals.setdefault(span["field"], []).append((span["start"], span["end"]))
        for field in doc["fields"]:
            for start, end in union_ranges(intervals.get(field["name"], [])):
                text = field["text"][start:end]
                for match in re.finditer(
                    r"(?:原告|被告|上诉人|被上诉人|申请人|第三人)[^，。；：\n]{0,20}?"
                    r"(?:主张|辩称|诉称|述称|表示)",
                    text,
                ):
                    hints.append(
                        {
                            "code": "party_reporting_cue_in_direct_finding",
                            "context_id": context["id"],
                            "field": field["name"],
                            "start": start + match.start(),
                            "end": start + match.end(),
                        }
                    )
    save(
        "header-diagnostics",
        {
            "diagnostic_version": "scoped-header-review-hints-v1",
            "hints": hints,
            "candidate_modified": False,
            "acceptance_modified": False,
            "independent_semantic_review": False,
        },
    )


def extract(doc, prompt, contract, provenance, infer, save):
    return scoped.extract(
        doc,
        prompt,
        contract,
        provenance,
        infer,
        save,
        merge=merge_body,
        on_header=header_diagnostics,
    )
