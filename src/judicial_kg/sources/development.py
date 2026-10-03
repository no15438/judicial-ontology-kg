"""Prepare development-only source records in Git-ignored output directories."""

import hashlib
import json
import subprocess
from datetime import UTC, datetime
from pathlib import Path

from judicial_kg.sources.appealcase import build_development
from judicial_kg.sources.records import evidence_span, sha256, verify_evidence
from judicial_kg.sources.spark import adapt_scan


def safe_output(root: Path, output: Path) -> Path:
    root, output = root.resolve(), output.resolve()
    allowed = [root / "artifacts", root / "data/derived"]
    if not any(output.is_relative_to(path) for path in allowed):
        raise ValueError("Source text outputs must stay under artifacts/ or data/derived/")
    return output


def write_json(path: Path, value) -> None:
    if path.is_symlink():
        raise ValueError("Refusing to overwrite a symlink output")
    # Replace the directory entry instead of following or mutating an existing file.
    temporary = path.with_suffix(path.suffix + ".tmp")
    with temporary.open("x", encoding="utf-8") as handle:
        json.dump(value, handle, ensure_ascii=False, indent=2)
        handle.write("\n")
    temporary.replace(path)


def prepare(root: Path, output: Path, spark_capture: Path | None = None) -> dict:
    root = root.resolve()
    output = safe_output(root, output)
    output.mkdir(parents=True, exist_ok=True)
    docs, appeal = build_development(
        root / "data/raw/appealcase/AppealCase.json",
        root / "data/raw/appealcase/AppealCase.metadata.json",
        root / "research/development-exposure.json",
    )
    groups = appeal.pop("group_manifest")
    write_json(output / "appealcase-documents.json", docs)
    write_json(output / "appealcase-groups.json", groups)
    write_json(output / "appealcase-report.json", appeal)
    spark = None
    if spark_capture is not None:
        raw_capture = spark_capture.read_bytes()
        spark_docs, spark = adapt_scan(json.loads(raw_capture))
        replayed = 0
        for doc in spark_docs:
            field = doc["fields"][0]
            if field["text"]:
                span = evidence_span(doc, field["name"], 0, len(field["text"]))
                verify_evidence(doc, span)
                replayed += 1
        spark["normalized_fields_replayed"] = replayed
        spark["capture_sha256"] = sha256(raw_capture)
        write_json(output / "spark-documents.json", spark_docs)
        write_json(output / "spark-report.json", spark)
    code_files = sorted((root / "src/judicial_kg").rglob("*.py"))
    code = {str(p.relative_to(root)): sha256(p.read_bytes()) for p in code_files}
    result = {
        "schema_version": 1,
        "observed_at_utc": datetime.now(UTC).isoformat(),
        "code_commit": subprocess.check_output(
            ["git", "rev-parse", "HEAD"], cwd=root, text=True
        ).strip(),
        "worktree_dirty": bool(subprocess.check_output(["git", "status", "--porcelain"], cwd=root)),
        "code_sha256": code,
        "exposure_manifest_sha256": sha256(
            (root / "research/development-exposure.json").read_bytes()
        ),
        "appealcase": appeal,
        "spark": spark,
        "outputs": {
            p.name: hashlib.file_digest(p.open("rb"), "sha256").hexdigest()
            for p in sorted(output.glob("*.json"))
            if p.name != "run.json"
        },
        "gold_available": False,
        "semantic_precision_recall_f1": None,
        "requires_user_legal_annotation": False,
    }
    write_json(output / "run.json", result)
    return result
