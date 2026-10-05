"""Offline partial decision ledger: resolved positions never imply acceptance."""

import json
from collections import Counter

from jsonschema import Draft202012Validator

from judicial_kg.contracts import Contract
from judicial_kg.extraction import contextual, selector_repair
from judicial_kg.extraction.draft import DraftError
from judicial_kg.extraction.repair_replay import replay_prepared
from judicial_kg.extraction.run import completion_content
from judicial_kg.sources.development import safe_output, write_json
from judicial_kg.sources.records import canonical_hash, sha256
from judicial_kg.validation import validate_record
from judicial_kg.validation_run import strict_json

VERSION = "selector-stage-v1"


def prepare_stages(root, documents, specification, saved, output):
    output = safe_output(root, output)
    if output.exists():
        raise ValueError("Selector stage requires a new output directory")
    report = strict_json((saved / "report.json").read_bytes())
    version = report["config"]["version"]
    parent = selector_repair.prepare_repair(root, documents, specification, version)
    proof = replay_prepared(root, documents, saved, output / "parent-replay", parent, version)
    if not proof["accepted_candidates_match_saved"] or not proof["outcomes_match_saved"]:
        raise ValueError("Parent selector replay mismatch")
    report = strict_json((saved / "report.json").read_bytes())
    selected = strict_json((saved / "selected-documents.json").read_bytes())
    calls = [c for c in report["response_metadata"] if c["path"] == "/v1/chat/completions"]
    contract = Contract.load(root, "0.2.0")
    stages = {}
    for doc, call in zip(selected, calls, strict=True):
        key = doc["document_id"]
        raw = (saved / f"rpc-{call['rpc']:03}.response.bin").read_bytes()
        choices = None
        try:
            content, _ = completion_content(strict_json(raw), report["config"]["model"])
            try:
                choices = strict_json(content)
            except (TypeError, ValueError):
                raise DraftError("selector_stage_invalid_json") from None
            if hasattr(parent, "bound_schema") and list(
                Draft202012Validator(parent.bound_schema(doc)).iter_errors(choices)
            ):
                raise DraftError("selector_repair_schema_invalid")
            patched, counts, pending = selector_repair.stage_choices(
                parent.drafts[key], parent.plans[key], choices
            )
        except DraftError as error:
            # Invalid decisions are never converted to uncertainty or applied in
            # part. Keep this entire document unchanged and continue other docs.
            stages[key] = {
                "original_draft_sha256": canonical_hash(parent.drafts[key]),
                "staged_draft_sha256": canonical_hash(parent.drafts[key]),
                "draft": parent.drafts[key],
                "decisions": choices,
                "decision_counts": {},
                "pending_decisions": [],
                "validation_issues": error.issues,
                "status": "invalid_parent_decisions",
                "reference_status": "model_decisions_not_gold",
            }
            continue
        # Full checks are always run. Unresolved decisions remain explicitly pending
        # even when unchanged selectors might happen to compile under a later rule.
        try:
            record = contextual.materialize(
                content=json.dumps(patched),
                doc=doc,
                contract=contract,
                provenance={
                    "method": "model",
                    "producer": report["config"]["model"],
                    "version": report["service_snapshot"]["models_sha256"],
                    "run_id": report["run_id"],
                },
            )
            issues = validate_record(record, doc, contract)
        except DraftError as error:
            issues = error.issues
        stages[key] = {
            "original_draft_sha256": canonical_hash(parent.drafts[key]),
            "staged_draft_sha256": canonical_hash(patched),
            "draft": patched,
            "decisions": choices,
            "decision_counts": counts,
            "pending_decisions": pending,
            "validation_issues": issues,
            "status": "unresolved" if pending or issues else "validated_parent_candidate",
            "reference_status": "model_decisions_not_gold",
        }
    write_json(output / "stages.json", stages)
    summary = {
        "version": VERSION,
        "requests_submitted": 0,
        "expected_documents": report["expected_documents"],
        "selected_documents": len(stages),
        "document_status_counts": dict(Counter(s["status"] for s in stages.values())),
        "parent_report_sha256": sha256((saved / "report.json").read_bytes()),
        "parent_replay_verified": True,
        "resolved_decisions": sum(
            sum(v for k, v in s["decision_counts"].items() if k != "uncertain")
            for s in stages.values()
        ),
        "pending_decisions": sum(len(s["pending_decisions"]) for s in stages.values()),
        "stages_sha256": sha256((output / "stages.json").read_bytes()),
        "semantic_precision_recall_f1": None,
    }
    write_json(output / "report.json", summary)
    return summary
