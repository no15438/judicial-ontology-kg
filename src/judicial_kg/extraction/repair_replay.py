"""Verify saved bounded-repair decisions offline without another inference request."""

import json
import subprocess
from collections import Counter
from datetime import UTC, datetime
from pathlib import Path

from judicial_kg.contracts import COLLECTIONS, Contract
from judicial_kg.extraction.draft import DraftError
from judicial_kg.extraction.mention_repair import VERSION, prepare_repair
from judicial_kg.extraction.run import busy, completion_content
from judicial_kg.sources.development import safe_output, write_json
from judicial_kg.sources.records import canonical_hash, sha256
from judicial_kg.validation import validate_record
from judicial_kg.validation_run import strict_json, validate_batch


def replay_repairs(root, document_paths, source_run, baseline, saved_repair, output):
    protocol = prepare_repair(root, document_paths, source_run, baseline)
    return replay_prepared(root, document_paths, saved_repair, output, protocol, VERSION)


def replay_prepared(root, document_paths, saved_repair, output, protocol, version):
    root = root.resolve()
    output = safe_output(root, output)
    if output.exists():
        raise ValueError("Repair replay requires a new output directory")
    original_raw = (saved_repair / "report.json").read_bytes()
    original = strict_json(original_raw)
    if not original["completed"] or original["config"]["version"] != version:
        raise ValueError("Replay requires a completed current-version repair run")
    if original["repair_lineage"] != protocol.lineage:
        raise ValueError("Repair replay source lineage mismatch")
    contract = Contract.load(root, protocol.CONTRACT_VERSION)
    schema = protocol.draft_schema(contract)
    if canonical_hash(schema) != original["draft_schema_sha256"]:
        raise ValueError("Repair response schema changed")

    def read_verified(name, as_json=True):
        if Path(name).name != name:
            raise ValueError("Invalid saved repair filename")
        raw = (saved_repair / name).read_bytes()
        if sha256(raw) != original["outputs_sha256"][name]:
            raise ValueError("Saved repair artifact hash mismatch")
        return strict_json(raw) if as_json else raw

    docs, inputs = [], []
    for path in document_paths:
        raw = path.read_bytes()
        docs.extend(strict_json(raw))
        inputs.append({"sha256": sha256(raw), "bytes": len(raw)})
    if inputs != original["input_files"]:
        raise ValueError("Repair replay must retain the exact full input manifests")
    validate_batch(docs, [], contract)
    selected = read_verified("selected-documents.json")
    by_id = {d["document_id"]: d for d in docs}
    protocol.check_selection(selected, original["config"])
    if any(doc != by_id.get(doc["document_id"]) for doc in selected):
        raise ValueError("Repair selection differs from source documents")
    if read_verified("repair-plans.json") != protocol.plans:
        raise ValueError("Repair source-choice plan changed")
    if read_verified("original-drafts.json") != protocol.drafts:
        raise ValueError("Original drafts changed")
    prompt = read_verified("prompt.txt", False).decode()
    calls = [c for c in original["response_metadata"] if c["path"] == "/v1/chat/completions"]
    metrics = [c for c in original["response_metadata"] if c["path"] == "/metrics"]
    if len(metrics) != len(selected) or original["requests_submitted"] != len(calls):
        raise ValueError("Repair replay requires one verified load receipt per selected document")
    responses = []
    skipped = []
    original_outcomes = {o["document_id"]: o for o in original["selected_outcomes"]}
    for index, (doc, metric) in enumerate(zip(selected, metrics, strict=True)):
        stem = f"rpc-{metric['rpc']:03}"
        load_request = read_verified(stem + ".request.json")
        if (
            metric["status"] != 200
            or metric["error"]
            or load_request != {"path": "/metrics", "body": None}
        ):
            raise ValueError("Repair replay load receipt invalid")
        load_raw = read_verified(stem + ".response.bin", False)
        if sha256(load_raw) != metric["response_sha256"]:
            raise ValueError("Repair replay load receipt hash mismatch")
        next_rpc = metrics[index + 1]["rpc"] if index + 1 < len(metrics) else float("inf")
        document_calls = [c for c in calls if metric["rpc"] < c["rpc"] < next_rpc]
        if busy(load_raw.decode()):
            outcome = original_outcomes[doc["document_id"]]
            if (
                document_calls
                or outcome["status"] != "service_busy"
                or outcome["issues"] != [{"code": "service_busy", "path": "/"}]
            ):
                raise ValueError("Busy skip differs from saved load receipt")
            responses.append(None)
            skipped.append(doc["document_id"])
            continue
        if len(document_calls) != 1:
            raise ValueError("Non-busy repair replay requires exactly one response per document")
        call = document_calls[0]
        stem = f"rpc-{call['rpc']:03}"
        request = read_verified(stem + ".request.json")
        if request["path"] != call["path"] or request["body"]["messages"] != protocol.messages(
            doc, prompt, schema
        ):
            raise ValueError("Saved repair response is not bound to this document and plan")
        expected_format = (
            protocol.response_format(doc, schema)
            if hasattr(protocol, "response_format")
            else {"type": "json_object"}
        )
        if request["body"].get("response_format") != expected_format:
            raise ValueError("Saved repair generation constraints differ from frozen catalog")
        responses.append(read_verified(stem + ".response.bin"))
    expected = read_verified("accepted.json")
    output.mkdir(parents=True, exist_ok=False)
    protocol.save_plan(output)
    outcomes = {
        d["document_id"]: {"document_id": d["document_id"], "status": "not_selected", "issues": []}
        for d in docs
    }
    records = []
    for doc, payload in zip(selected, responses, strict=True):
        outcome = outcomes[doc["document_id"]]
        if payload is None:
            outcome.update(status="service_busy", issues=[{"code": "service_busy", "path": "/"}])
            continue
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
    result = validate_batch(docs, lines, contract)
    (output / "candidates.jsonl").write_text("".join(line + "\n" for line in lines))
    write_json(output / "accepted.json", result["accepted_records"])
    write_json(output / "outcomes.json", list(outcomes.values()))
    write_json(
        output / "validation-ledger.json", {k: result[k] for k in ("attempts", "document_outcomes")}
    )
    report = {
        "run_kind": "offline_repair_replay_not_new_inference",
        "completed": True,
        "observed_at_utc": datetime.now(UTC).isoformat(),
        "requests_submitted": 0,
        "source_repair_report_sha256": sha256(original_raw),
        "repair_lineage": protocol.lineage,
        "code_commit": subprocess.check_output(
            ["git", "rev-parse", "HEAD"], cwd=root, text=True
        ).strip(),
        "worktree_dirty": bool(subprocess.check_output(["git", "status", "--porcelain"], cwd=root)),
        "code_sha256": {
            str(p.relative_to(root)): sha256(p.read_bytes())
            for p in sorted((root / "src/judicial_kg").rglob("*.py"))
        },
        **contract.versions(),
        "input_files": inputs,
        "validation": result["summary"],
        "expected_documents": len(docs),
        "selected_documents": len(selected),
        "selected_outcomes": [outcomes[d["document_id"]] for d in selected],
        "document_status_counts": dict(Counter(o["status"] for o in outcomes.values())),
        "repair_decision_counts": protocol.decision_counts,
        "accepted_candidates_match_saved": result["accepted_records"] == expected,
        "outcomes_match_saved": all(
            all(outcomes[o["document_id"]][k] == o[k] for k in ("status", "issues"))
            for o in original["selected_outcomes"]
        ),
        "outputs_sha256": {p.name: sha256(p.read_bytes()) for p in sorted(output.iterdir())},
        "gold_available": False,
        "semantic_precision_recall_f1": None,
        "requires_user_legal_annotation": False,
    }
    if skipped:
        report["busy_skips_verified_without_inference"] = skipped
    write_json(output / "report.json", report)
    return report
