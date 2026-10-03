"""Account for every expected development document and every extraction attempt."""

import json
import subprocess
import sys
from collections import Counter
from datetime import UTC, datetime
from importlib.metadata import version
from pathlib import Path

from judicial_kg.contracts import COLLECTIONS, Contract
from judicial_kg.sources.development import safe_output, write_json
from judicial_kg.sources.records import sha256
from judicial_kg.validation import validate_record, validate_source


def strict_json(raw):
    def invalid_constant(_):
        raise ValueError("Non-finite JSON constant")

    def unique_keys(pairs):
        result = {}
        for key, value in pairs:
            if key in result:
                raise ValueError("Duplicate JSON object key")
            result[key] = value
        return result

    return json.loads(raw, parse_constant=invalid_constant, object_pairs_hook=unique_keys)


def validate_batch(documents: list[dict], lines: list[str], contract: Contract) -> dict:
    expected = {}
    for doc in documents:
        validate_source(doc)
        if doc.get("allocation") != "development_exposed":
            raise ValueError("Only explicitly exposed development documents are allowed")
        if doc["document_id"] in expected:
            raise ValueError("Duplicate expected document; cannot preserve denominator")
        expected[doc["document_id"]] = doc
    if not expected:
        raise ValueError("Expected document manifest must not be empty")
    attempts, grouped = [], {}
    for index, line in enumerate(lines, 1):
        attempt = {"line": index, "status": "rejected", "issues": []}
        attempts.append(attempt)
        try:
            record = strict_json(line)
        except (ValueError, TypeError):
            attempt["issues"] = [{"code": "invalid_json", "path": "/"}]
            continue
        doc_id = record.get("document_id") if isinstance(record, dict) else None
        if not isinstance(doc_id, str) or doc_id not in expected:
            attempt["issues"] = [{"code": "unknown_document", "path": "/document_id"}]
            continue
        attempt["document_id"] = doc_id
        attempt["issues"] = validate_record(record, expected[doc_id], contract)
        grouped.setdefault(doc_id, []).append((attempt, record))
    accepted, outcomes = [], []
    for doc_id in expected:
        candidates = grouped.get(doc_id, [])
        outcome = {"document_id": doc_id, "attempt_lines": [a["line"] for a, _ in candidates]}
        if not candidates:
            outcome["status"] = "missing"
        elif len(candidates) > 1:
            outcome["status"] = "rejected"
            for attempt, _ in candidates:
                attempt["issues"].append({"code": "duplicate_document_attempt", "path": "/"})
        else:
            attempt, record = candidates[0]
            if attempt["issues"]:
                outcome["status"] = "rejected"
            else:
                empty = not any(record[key] for key in COLLECTIONS)
                outcome["status"] = "accepted_empty" if empty else "accepted_candidate"
                attempt["status"] = outcome["status"]
                accepted.append(record)
        outcomes.append(outcome)
    counts = Counter(x["status"] for x in outcomes)
    issues = Counter(i["code"] for a in attempts for i in a["issues"])
    return {
        "accepted_records": accepted,
        "document_outcomes": outcomes,
        "attempts": attempts,
        "summary": {
            "expected_documents": len(expected),
            "source_fields_verified": sum(len(doc["fields"]) for doc in documents),
            "attempts_total": len(attempts),
            "attempts_rejected": sum(a["status"] == "rejected" for a in attempts),
            "documents_accepted_candidate": counts["accepted_candidate"],
            "documents_accepted_empty": counts["accepted_empty"],
            "documents_rejected": counts["rejected"],
            "documents_missing": counts["missing"],
            "issue_counts": dict(sorted(issues.items())),
            "accepted_node_counts": {
                key: sum(len(r[key]) for r in accepted) for key in COLLECTIONS
            },
            "accepted_evidence_spans_replayed": sum(
                1 if key == "mentions" else len(node["evidence"])
                for record in accepted
                for key in COLLECTIONS
                if key != "actors"
                for node in record[key]
            ),
            "semantic_precision_recall_f1": None,
            "gold_available": False,
            "semantic_completeness": "not_assessed",
            "requires_user_legal_annotation": False,
        },
    }


def run_validation(root: Path, document_paths: list[Path], candidates: Path, output: Path) -> dict:
    root = root.resolve()
    output = safe_output(root, output)
    inputs = [p.resolve() for p in [*document_paths, candidates]]
    destinations = [output / name for name in ("accepted.json", "ledger.json", "report.json")]
    if any(p in destinations for p in inputs):
        raise ValueError("Validation output must not overwrite an input")
    contract = Contract.load(root)
    documents, input_files = [], []
    for path in document_paths:
        raw = path.read_bytes()
        value = strict_json(raw)
        if not isinstance(value, list):
            raise ValueError("Expected a JSON array of source documents")
        documents.extend(value)
        input_files.append({"kind": "documents", "sha256": sha256(raw), "bytes": len(raw)})
    raw = candidates.read_bytes()
    input_files.append({"kind": "candidates", "sha256": sha256(raw), "bytes": len(raw)})
    result = validate_batch(documents, raw.decode("utf-8").splitlines(), contract)
    output.mkdir(parents=True, exist_ok=True)
    write_json(output / "accepted.json", result["accepted_records"])
    write_json(output / "ledger.json", {k: result[k] for k in ("document_outcomes", "attempts")})
    source_files = sorted((root / "src/judicial_kg").rglob("*.py"))
    report = {
        "schema_version": 1,
        "run_kind": "development_contract_validation_not_semantic_evaluation",
        "observed_at_utc": datetime.now(UTC).isoformat(),
        **contract.versions(),
        "code_commit": subprocess.check_output(
            ["git", "rev-parse", "HEAD"], cwd=root, text=True
        ).strip(),
        "worktree_dirty": bool(subprocess.check_output(["git", "status", "--porcelain"], cwd=root)),
        "code_sha256": {str(p.relative_to(root)): sha256(p.read_bytes()) for p in source_files},
        "runtime": {
            "python": sys.version.split()[0],
            "jsonschema": version("jsonschema"),
            "dependency_lock_sha256": sha256((root / "uv.lock").read_bytes()),
        },
        "inputs": input_files,
        "outputs": {p.name: sha256(p.read_bytes()) for p in destinations[:2]},
        **result["summary"],
    }
    write_json(output / "report.json", report)
    return report
