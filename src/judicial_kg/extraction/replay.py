"""Offline recompilation of complete saved v5 runs; never submit model requests."""

import json
import subprocess
from collections import Counter
from datetime import UTC, datetime
from pathlib import Path

from judicial_kg.contracts import COLLECTIONS, Contract
from judicial_kg.extraction import contextual, typed
from judicial_kg.extraction.draft import DraftError
from judicial_kg.extraction.run import completion_content
from judicial_kg.sources.development import safe_output, write_json
from judicial_kg.sources.records import canonical_hash, sha256
from judicial_kg.validation import validate_record
from judicial_kg.validation_run import strict_json, validate_batch


def replay_completed(root: Path, document_paths: list[Path], source_run: Path, output: Path):
    root, source_run = root.resolve(), source_run.resolve()
    output = safe_output(root, output)
    original_bytes = (source_run / "report.json").read_bytes()
    original = strict_json(original_bytes)
    protocols = {"qwen-extraction-v5": typed, "qwen-extraction-v6": contextual}
    if not original["completed"] or original["config"]["version"] not in protocols:
        raise ValueError("Replay requires a completed v5 or v6 run")
    protocol = protocols[original["config"]["version"]]
    contract = Contract.load(root, getattr(protocol, "CONTRACT_VERSION", "0.1.0"))
    if original["ontology_version"] != contract.ontology["version"]:
        raise ValueError("Saved run contract and protocol disagree")

    def read_verified(name):
        if Path(name).name != name:
            raise ValueError("Invalid saved output filename")
        raw = (source_run / name).read_bytes()
        if sha256(raw) != original["outputs_sha256"][name]:
            raise ValueError("Saved run artifact hash mismatch")
        return strict_json(raw)

    docs, inputs = [], []
    for path in document_paths:
        raw = path.read_bytes()
        docs.extend(strict_json(raw))
        inputs.append({"sha256": sha256(raw), "bytes": len(raw)})
    if inputs != original["input_files"] or len(docs) != original["expected_documents"]:
        raise ValueError("Replay must retain the exact original input denominator")
    validate_batch(docs, [], contract)
    selected = read_verified("selected-documents.json")
    by_id = {d["document_id"]: d for d in docs}
    if any(d != by_id.get(d["document_id"]) for d in selected):
        raise ValueError("Selected source document differs from original input")
    saved_ids = [o["document_id"] for o in original["selected_outcomes"]]
    if [d["document_id"] for d in selected] != saved_ids or len(set(saved_ids)) != len(saved_ids):
        raise ValueError("Saved selection is inconsistent")
    calls = [r for r in original["response_metadata"] if r["path"] == "/v1/chat/completions"]
    if len(calls) != len(selected) or original["requests_submitted"] != len(selected):
        raise ValueError("Replay requires one saved response per selected document")
    responses = []
    for doc, call in zip(selected, calls, strict=True):
        prefix = f"rpc-{call['rpc']:03}"
        request = read_verified(prefix + ".request.json")
        # Bind response order to the exact source payload, not just array position.
        source = protocol.messages(doc, "", {})[-1]
        if request["path"] != call["path"] or request["body"]["messages"][-1] != source:
            raise ValueError("Saved request is not bound to this source document")
        responses.append(read_verified(prefix + ".response.bin"))
    # Validate all lineage inputs before creating any derived output.
    output.mkdir(parents=True, exist_ok=False)
    outcomes = {
        d["document_id"]: {"document_id": d["document_id"], "status": "not_selected", "issues": []}
        for d in docs
    }
    records = []
    for doc, payload in zip(selected, responses, strict=True):
        outcome = outcomes[doc["document_id"]]
        try:
            content, _ = completion_content(payload, original["config"]["model"])
            record = protocol.materialize(
                content,
                doc,
                contract,
                {
                    "method": "model",
                    "producer": original["config"]["model"],
                    "version": original["service_snapshot"]["models_sha256"],
                    "run_id": original["run_id"],
                },
            )
            issues = validate_record(record, doc, contract)
            records.append(record)
            outcome.update(
                status="contract_rejected"
                if issues
                else "accepted_candidate"
                if any(record[k] for k in COLLECTIONS)
                else "accepted_empty",
                issues=issues,
            )
        except DraftError as error:
            outcome.update(status=error.code, issues=error.issues)
    lines = [json.dumps(r, ensure_ascii=False) for r in records]
    (output / "candidates.jsonl").write_text("".join(line + "\n" for line in lines))
    result = validate_batch(docs, lines, contract)
    write_json(output / "accepted.json", result["accepted_records"])
    write_json(output / "outcomes.json", list(outcomes.values()))
    write_json(
        output / "validation-ledger.json", {k: result[k] for k in ("document_outcomes", "attempts")}
    )
    report = {
        "schema_version": 1,
        "run_kind": "offline_qwen_response_recompilation_not_new_inference",
        "completed": True,
        "observed_at_utc": datetime.now(UTC).isoformat(),
        "requests_submitted": 0,
        "source_run_id": original["run_id"],
        "source_report_sha256": sha256(original_bytes),
        "source_response_sha256": [r["response_sha256"] for r in calls],
        "original_document_status_counts": original["document_status_counts"],
        "input_files": inputs,
        "expected_documents": len(docs),
        "selected_documents": len(selected),
        "selected_outcomes": [outcomes[d["document_id"]] for d in selected],
        "document_status_counts": dict(Counter(o["status"] for o in outcomes.values())),
        "code_commit": subprocess.check_output(
            ["git", "rev-parse", "HEAD"], cwd=root, text=True
        ).strip(),
        "worktree_dirty": bool(subprocess.check_output(["git", "status", "--porcelain"], cwd=root)),
        "code_sha256": {
            str(p.relative_to(root)): sha256(p.read_bytes())
            for p in sorted((root / "src/judicial_kg").rglob("*.py"))
        },
        "draft_schema_sha256": canonical_hash(protocol.draft_schema(contract)),
        "value_normalizer_version": protocol.NORMALIZER_VERSION,
        **contract.versions(),
        "validation": result["summary"],
        "outputs_sha256": {p.name: sha256(p.read_bytes()) for p in sorted(output.iterdir())},
        "original_run_unmodified": True,
        "gold_available": False,
        "semantic_precision_recall_f1": None,
        "requires_user_legal_annotation": False,
    }
    write_json(output / "report.json", report)
    return report
