"""Read a QA delivery's small evidence files; never scan its full data shards.

Can also run via SSH stdin with Python's standard library; writes only to stdout.
"""

import argparse
import hashlib
import json
from collections import Counter
from datetime import UTC, datetime
from io import BytesIO
from pathlib import Path
from xml.etree import ElementTree
from zipfile import ZipFile

SAMPLE_PATH = "sample/standardized_qa_dataset_sample_100.json"
SPEC_PATH = "docs/法律文书高质量数据集_V1.1_QA展开数据补充规范.docx"
MAX_EVIDENCE_BYTES = 3_000_000


def digest(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


def sample_summary(rows: list[dict]) -> dict:
    """Count QA rows and parents separately, without exporting their text or answers."""
    if not isinstance(rows, list) or not rows:
        raise ValueError("Expected a nonempty QA sample array")
    parents = Counter()
    exposure = {}
    checks = Counter()
    distributions = {
        key: Counter()
        for key in (
            "human_review_status",
            "label_method",
            "annotator",
            "license",
            "generated_flag",
            "generated_data_indicator",
            "qa_constructed_flag",
            "case_type",
            "qa_field_key",
            "qa_source_basis",
            "qa_review_status",
        )
    }
    for row in rows:
        text = row["text"]
        source_id = row["source_detail"]["source_id"]
        if not isinstance(text, str) or not isinstance(source_id, str) or not source_id:
            raise ValueError("Expected text and a nonempty string parent source_id")
        parent_key = digest(source_id.encode("utf-8"))
        text_hash = digest(text.encode("utf-8"))
        parents[parent_key] += 1
        exposure[(parent_key, text_hash)] = {
            "source_id_sha256": parent_key,
            "text_sha256": text_hash,
            "declared_raw_text_sha256": row["content"]["raw_text_sha256"],
        }
        qa = row["qa_pairs"]
        if not isinstance(qa, list):
            raise ValueError("Expected qa_pairs array")
        row_checks = {
            "text_sha256_matches": text_hash == row["content"]["text_sha256"],
            "text_length_matches": len(text) == row["content"]["text_length"],
            "source_standardized_hash_matches": (
                text_hash == row["source_detail"]["standardized_text_hash"]
            ),
            "single_qa": len(qa) == 1,
            "qa_annotation_mirror_matches": qa == row["annotation"]["qa_pairs"],
            "full_text_mirror_matches": (
                isinstance(row["data_content"], list)
                and len(row["data_content"]) == 1
                and row["data_content"][0]["content"] == text
            ),
            "declared_raw_hash_differs": row["content"]["raw_text_sha256"] != text_hash,
        }
        for key, passed in row_checks.items():
            checks[key] += int(passed)
        for key in [
            "human_review_status",
            "license",
            "generated_flag",
            "generated_data_indicator",
            "qa_constructed_flag",
            "case_type",
        ]:
            distributions[key][json.dumps(row[key], ensure_ascii=False)] += 1
        for key in ["label_method", "annotator"]:
            distributions[key][row["label_info"][key]] += 1
        for pair in qa:
            distributions["qa_field_key"][pair["field_key"]] += 1
            distributions["qa_source_basis"][pair["source_basis"]] += 1
            distributions["qa_review_status"][pair["review_status"]] += 1
    return {
        "records": len(rows),
        "unique_qa_ids": len({row["id"] for row in rows}),
        "unique_parent_source_ids": len(parents),
        "unique_texts": len({row["text"] for row in rows}),
        "qa_rows_per_parent": sorted(parents.values()),
        "private_lending_rows": sum(row["case_type"] == "民间借贷纠纷" for row in rows),
        "observed_fields": sorted({key for row in rows for key in row}),
        "checks_passing_rows": dict(checks),
        "distributions": {key: dict(sorted(value.items())) for key, value in distributions.items()},
        "exposed_parent_text_keys": sorted(exposure.values(), key=lambda x: x["source_id_sha256"]),
        "exposure_policy": "All inspected parents are development-exposed; no blind test use",
        "human_review_independently_verified": False,
        "gold_admitted": False,
    }


def inventory(root: Path) -> dict:
    root = root.resolve(strict=True)
    evidence = []

    def read(relative: str) -> bytes:
        path = (root / relative).resolve(strict=True)
        if not path.is_relative_to(root):
            raise ValueError(f"Evidence path escapes delivery: {relative}")
        with path.open("rb") as handle:
            raw = handle.read(MAX_EVIDENCE_BYTES + 1)
        if len(raw) > MAX_EVIDENCE_BYTES:
            raise ValueError(f"Evidence exceeds small-file limit: {relative}")
        evidence.append({"path": relative, "bytes": len(raw), "sha256": digest(raw)})
        return raw

    files = []
    for path in sorted(root.rglob("*")):
        if path.is_symlink():
            files.append({"path": str(path.relative_to(root)), "symlink": True})
        elif path.is_file():
            files.append({"path": str(path.relative_to(root)), "bytes": path.stat().st_size})
    rows = json.loads(read(SAMPLE_PATH))
    manifest = json.loads(read("manifests/shard_manifest.json"))
    summary = json.loads(read("reports/run_summary.json"))
    historical_audit = json.loads(read("reports/remote_audit.json"))
    checksum = read("manifests/checksum_manifest.txt").decode("utf-8")
    # These are evidence hashes, not certification of assertions in the reports.
    for relative in [
        "reports/final_acceptance_report.md",
        "reports/local_full_audit.json",
        "reports/standardized_qa_dataset_10500000_validation.json",
        "reports/audit_checksum_manifest.txt",
        "state/export_checkpoint.json",
    ]:
        read(relative)
    with ZipFile(BytesIO(read(SPEC_PATH))) as archive:
        if archive.getinfo("word/document.xml").file_size > MAX_EVIDENCE_BYTES:
            raise ValueError("Specification XML exceeds small-file limit")
        xml = ElementTree.fromstring(archive.read("word/document.xml"))
    ns = {"w": "http://schemas.openxmlformats.org/wordprocessingml/2006/main"}
    paragraphs = [
        "".join(t.text or "" for t in p.findall(".//w:t", ns)) for p in xml.findall(".//w:p", ns)
    ]
    checksum_entries = [line.split(None, 1)[::-1] for line in checksum.splitlines() if line]
    declared_checksums = dict(checksum_entries)
    shards = [f for f in files if f["path"].startswith("data/") and f["path"].endswith(".json")]
    expected_shards = {"data/" + Path(s["remote"]).name: s for s in manifest["shards"]}
    if not expected_shards or len(expected_shards) != len(manifest["shards"]):
        raise ValueError("Expected nonempty, unique shard paths in manifest")
    if len(declared_checksums) != len(checksum_entries):
        raise ValueError("Duplicate paths in checksum manifest")
    return {
        "schema_version": 1,
        "observed_at_utc": datetime.now(UTC).isoformat(),
        "source_root": str(root),
        "scope": "File stats, small metadata, specification text, and supplied 100-row sample",
        "files": files,
        "observed_shards": {
            "files": len(shards),
            "bytes": sum(f.get("bytes", 0) for f in shards),
            "paths_match_manifest": {f["path"] for f in shards} == set(expected_shards),
            "checksum_declarations_match_manifest": (
                set(declared_checksums) == set(expected_shards)
                and all(
                    declared_checksums.get(path) == s["sha256"]
                    for path, s in expected_shards.items()
                )
            ),
            "fresh_shard_hashes_checked": 0,
            "fresh_shard_records_parsed": 0,
        },
        "historical_claims_not_recomputed": {
            "records": summary["records"],
            "accepted_parents": summary["accepted_parents"],
            "sum_manifest_records": sum(s["records"] for s in manifest["shards"]),
            "remote_sha256_all_valid": historical_audit["sha256_all_valid"],
        },
        "specification_fixed_enum_paragraphs": [p for p in paragraphs if "固定为" in p],
        "sample": sample_summary(rows),
        "evidence_files": evidence,
        "not_verified": [
            "Full corpus hashes, row counts, private-lending population, or representativeness",
            "Court originals, collection/usage authorization, or masking offset maps",
            "Individual reviewer identity, dated audit trail, or annotation adjudication",
            "Semantic QA correctness or exhaustive entity/relation/event coverage",
        ],
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, required=True)
    args = parser.parse_args()
    print(json.dumps(inventory(args.root), ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
