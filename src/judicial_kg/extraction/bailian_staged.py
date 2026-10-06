"""Single-document, cost-bounded cloud execution of the shared staged protocol."""

import copy
import json
import subprocess
from decimal import Decimal
from pathlib import Path
from uuid import uuid4

from judicial_kg.contracts import Contract
from judicial_kg.extraction import (
    augmented_inventory,
    bounded_workflow,
    checked_forms,
    collective_mentions,
    context_delta,
    context_ids,
    described_actors,
    evidence_first,
    fragment_repairs,
    grouped_mentions,
    mention_stage,
    owner_first,
    space_inventory,
    typed_owner,
    unique_forms,
    value_stage,
)
from judicial_kg.extraction import compact_inventory as protocol
from judicial_kg.extraction.bailian import MODEL, PRICE_SOURCE, BailianClient, credentials
from judicial_kg.extraction.draft import DraftError
from judicial_kg.extraction.run import completion_content, extraction_lock, usage_counts
from judicial_kg.extraction.stage_resume import StagePrefix
from judicial_kg.extraction.transport import Response
from judicial_kg.sources.development import safe_output, write_json
from judicial_kg.sources.records import sha256
from judicial_kg.validation_run import strict_json, validate_batch

CONFIG = {
    "version": "bailian-staged-v16",
    "model": MODEL,
    "temperature": 0.2,
    "seed": 42,
    "max_tokens": 8192,
    "budget_cny": "5",
    "timeout_seconds": 300,
    "input_cny_per_million": "12",
    "output_cny_per_million": "36",
    "response_mode": "json_object_with_local_schema_validation",
}


def cost(input_tokens, output_tokens):
    return (Decimal(input_tokens) * 12 + Decimal(output_tokens) * 36) / 1000000


def select_document(documents, document_id=None):
    if document_id is None:
        return documents[0]
    matches = [d for d in documents if d["document_id"] == document_id]
    if len(matches) != 1:
        raise ValueError("Selected document ID must occur exactly once in the input denominator")
    return matches[0]


def run_cloud(
    root,
    paths,
    output,
    *,
    client=None,
    replay_origin=None,
    version=16,
    previous_runs=(),
    budget_cny=None,
    resume_from=None,
    restart_stage=None,
    document_id=None,
):
    with extraction_lock(root):
        return _run(
            root,
            paths,
            output,
            client=client,
            replay_origin=replay_origin,
            version=version,
            previous_runs=previous_runs,
            budget_cny=budget_cny,
            resume_from=resume_from,
            restart_stage=restart_stage,
            document_id=document_id,
        )


def _run(
    root,
    paths,
    output,
    *,
    client=None,
    replay_origin=None,
    version=16,
    previous_runs=(),
    budget_cny=None,
    resume_from=None,
    restart_stage=None,
    document_id=None,
):
    root = root.resolve()
    selected_protocol = {
        16: protocol,
        17: space_inventory,
        18: context_delta,
        19: context_ids,
        20: mention_stage,
        21: value_stage,
        22: evidence_first,
        23: owner_first,
        24: typed_owner,
        25: collective_mentions,
        26: grouped_mentions,
        27: checked_forms,
        28: fragment_repairs,
        29: described_actors,
        30: augmented_inventory,
        31: unique_forms,
        32: bounded_workflow,
    }[version]
    config = {**CONFIG, "version": f"bailian-staged-v{version}"}
    if budget_cny is not None:
        budget = Decimal(budget_cny)
        if not budget.is_finite() or not 0 < budget <= 200:
            raise ValueError("Budget must be positive and at most authorized 200 CNY")
        config["budget_cny"] = str(budget)
    lineage, prior = [], Decimal(0)
    prior_reports = []
    for path in previous_runs:
        path = path.resolve()
        raw = (path / "report.json").read_bytes()
        saved = strict_json(raw)
        prior_reports.append(saved)
        if not saved["completed"] or saved["config"]["model"] != MODEL:
            raise ValueError("Unsettled prior cloud run")
        prior += Decimal(saved["budget_committed_cny"])
        lineage.append(
            {
                "path": str(path.relative_to(root)),
                "sha256": sha256(raw),
                "committed_cny": saved["budget_committed_cny"],
            }
        )
    if len({x["path"] for x in lineage}) != len(lineage) or prior > Decimal(config["budget_cny"]):
        raise ValueError("Invalid prior budget lineage")
    for path in previous_runs:
        parent = strict_json((path / "report.json").read_bytes())
        if any(item not in lineage for item in parent.get("budget_lineage", [])):
            raise ValueError("Missing prior budget ancestor")
    docs, inputs = [], []
    for path in paths:
        raw = path.read_bytes()
        docs.extend(strict_json(raw))
        inputs.append({"sha256": sha256(raw), "bytes": len(raw)})
    contract = Contract.load(root, selected_protocol.CONTRACT_VERSION)
    validate_batch(docs, [], contract)
    if not docs:
        raise ValueError("No source documents")
    selected = select_document(docs, document_id)
    reuse = None
    if resume_from is not None:
        reuse = StagePrefix(root, resume_from, inputs, contract.versions(), config, restart_stage)
        if reuse.origin not in lineage:
            raise ValueError("Resume source must be included in the budget lineage")
        resume_attempt = 1 + sum(
            "resume_origin" in saved
            and saved.get("selected_document_id") == selected["document_id"]
            for saved in prior_reports
        )
        if resume_attempt > 3:
            raise ValueError("Resume attempt budget exhausted for this document")
    elif restart_stage is not None:
        raise ValueError("restart_stage requires resume_from")
    output = safe_output(root, output)
    output.mkdir(parents=True, exist_ok=False)
    client = client or BailianClient(*credentials(root))
    prompt = (root / selected_protocol.PROMPT_FILE).read_text()
    run_id = replay_origin["run_id"] if replay_origin else str(uuid4())
    report = {
        "run_id": run_id,
        "config": config,
        "completed": False,
        "code_commit": subprocess.check_output(
            ["git", "rev-parse", "HEAD"], cwd=root, text=True
        ).strip(),
        "worktree_dirty": bool(subprocess.check_output(["git", "status", "--porcelain"], cwd=root)),
        "input_files": inputs,
        "expected_documents": len(docs),
        "selected_documents": 1,
        "not_selected": len(docs) - 1,
        "selected_document_id": selected["document_id"],
        "requests_submitted": 0,
        "requests_replayed": 0,
        "stages": [],
        "observed_cost_cny": "0",
        "budget_committed_cny": "0",
        "price_source": PRICE_SOURCE,
        "price_verified_date": "2026-10-05",
        "prompt_sha256": sha256(prompt.encode()),
        "contract": contract.versions(),
        "semantic_precision_recall_f1": None,
        "requires_user_legal_annotation": False,
        "billing_note": "List-price estimate; not provider invoice or account-wide cap",
        "input_budget_method": "UTF-8 bytes plus 4096 allowance, not measured tokens",
    }
    if version != 16 or lineage:
        report.update(budget_lineage=lineage, prior_committed_cny=str(prior))
    if reuse is not None:
        report.update(
            resume_origin=reuse.origin,
            restart_stage=reuse.restart_stage,
            responses_reused=0,
            resume_attempt=resume_attempt,
            max_resume_attempts=3,
        )
    observed, committed = Decimal(0), Decimal(0)

    def save(name, value):
        write_json(output / (name + ".json"), value)

    save("selected-documents", [selected])
    save("config", config)
    (output / "prompt.txt").write_text(prompt)

    def checkpoint():
        report.update(observed_cost_cny=str(observed), budget_committed_cny=str(committed))
        save("report", report)

    def infer(messages, fmt, stage):
        nonlocal committed, observed
        messages = copy.deepcopy(messages)
        # JSON mode alone is not a structural guarantee: enforce the same full local schema.
        serialized_schema = json.dumps(fmt["schema"], ensure_ascii=False)
        if version < 32 or not any(serialized_schema in m["content"] for m in messages):
            messages[0]["content"] += "\n只输出JSON，遵守本阶段Schema：\n" + serialized_schema
        request = {
            "model": MODEL,
            "messages": messages,
            "temperature": 0.2,
            "seed": 42,
            "max_tokens": CONFIG["max_tokens"],
            "enable_thinking": False,
            "enable_search": False,
            "stream": False,
            "response_format": {"type": "json_object"},
        }
        upper = sum(len(m["content"].encode()) for m in messages) + 4096
        reservation = cost(upper, CONFIG["max_tokens"])
        row = {
            "stage": stage,
            "status": "planned",
            "input_upper_bound": upper,
            "reservation_cny": str(reservation),
        }
        report["stages"].append(row)
        save("request-" + stage, request)
        save("schema-" + stage, fmt["schema"])
        if reuse is not None:
            cached = reuse.take(stage, request, fmt["schema"])
            if cached is not None:
                raw, receipt, origin_row = cached
                (output / ("response-" + stage + ".bin")).write_bytes(raw)
                save("receipt-" + stage, receipt)
                row.update(
                    status="response_reused",
                    response_sha256=sha256(raw),
                    source_stage=stage,
                    source_response_cost_cny=origin_row.get("cost_cny"),
                    elapsed_seconds=0,
                    cost_cny="0",
                )
                report["responses_reused"] += 1
                checkpoint()
                content, _ = completion_content(strict_json(raw), MODEL)
                return content
        if upper > 120000:
            row["status"] = "input_budget_exceeded"
            checkpoint()
            raise DraftError(row["status"])
        if prior + committed + reservation > Decimal(config["budget_cny"]):
            row["status"] = "cost_budget_exceeded"
            checkpoint()
            raise DraftError(row["status"])
        committed += reservation
        report["requests_replayed" if replay_origin else "requests_submitted"] += 1
        row["status"] = "in_flight"
        checkpoint()
        response = client.call(request, CONFIG["timeout_seconds"])
        (output / ("response-" + stage + ".bin")).write_bytes(response.raw)
        row.update(
            http_status=response.status,
            transport_error=response.error,
            elapsed_seconds=response.elapsed_seconds,
            response_sha256=sha256(response.raw),
        )
        save(
            "receipt-" + stage,
            {
                "status": response.status,
                "error": response.error,
                "elapsed_seconds": response.elapsed_seconds,
            },
        )
        if response.error or response.status != 200:
            row["status"] = "transport_or_http_failed"
            checkpoint()
            raise DraftError(row["status"])
        payload = response.json()
        usage = usage_counts(payload)
        row["usage"] = usage
        if not {"prompt_tokens", "completion_tokens"} <= usage.keys():
            row["status"] = "usage_missing"
            checkpoint()
            raise DraftError(row["status"])
        actual = cost(usage["prompt_tokens"], usage["completion_tokens"])
        observed += actual
        if usage["prompt_tokens"] > upper or usage["completion_tokens"] > CONFIG["max_tokens"]:
            row["status"] = "provider_usage_exceeds_reservation"
            checkpoint()
            raise DraftError(row["status"])
        # Settle only successful responses with valid usage; failures retain full reservations.
        committed += actual - reservation
        row["cost_cny"] = str(actual)
        try:
            content, _ = completion_content(payload, MODEL)
            row["status"] = "response_complete"
            return content
        except DraftError as error:
            row["status"] = error.code
            raise
        finally:
            checkpoint()

    checkpoint()
    candidate = None
    try:
        candidate = selected_protocol.extract(
            selected,
            prompt,
            contract,
            {"method": "model", "producer": MODEL, "version": config["version"], "run_id": run_id},
            infer,
            save,
        )
        report["status"] = "accepted_candidate"
    except (DraftError, ValueError, KeyError, TypeError) as error:
        report["status"] = (
            error.code if isinstance(error, DraftError) else "protocol_invalid_response"
        )
        report["issues"] = getattr(error, "issues", [])
    if version >= 27:
        report["model_correction_calls"] = sum(
            s["stage"].endswith("-repair-1") for s in report["stages"]
        )
        report["first_pass_accepted"] = (
            candidate is not None and report["model_correction_calls"] == 0 and reuse is None
        )
    if reuse is not None:
        report["first_pass_accepted"] = False
        report["resume_cache_stop_reason"] = reuse.stop_reason
    save("accepted", [] if candidate is None else [candidate])
    report["completed"] = True
    report["outputs_sha256"] = {
        p.name: sha256(p.read_bytes())
        for p in output.iterdir()
        if p.is_file() and p.name != "report.json"
    }
    checkpoint()
    return report


def replay_cloud(root, paths, source_run, output):
    original = strict_json((source_run / "report.json").read_bytes())
    version = int(original["config"]["version"].rsplit("v", 1)[1])
    if not original["completed"] or original["config"] != {
        **CONFIG,
        "version": f"bailian-staged-v{version}",
        "budget_cny": original["config"]["budget_cny"],
    }:
        raise ValueError("Incomplete or changed cloud configuration")
    for name, digest in original["outputs_sha256"].items():
        if Path(name).name != name or sha256((source_run / name).read_bytes()) != digest:
            raise ValueError("Saved cloud artifact changed")
    if [
        {"sha256": sha256(p.read_bytes()), "bytes": len(p.read_bytes())} for p in paths
    ] != original["input_files"]:
        raise ValueError("Source denominator changed")
    calls = [s for s in original["stages"] if "http_status" in s]

    class SavedClient:
        count = 0

        def call(self, request, timeout):
            row = calls[self.count]
            self.count += 1
            stage = row["stage"]
            if strict_json((source_run / ("request-" + stage + ".json")).read_bytes()) != request:
                raise RuntimeError("Reconstructed cloud request changed")
            return Response(
                row["http_status"],
                (source_run / ("response-" + stage + ".bin")).read_bytes(),
                row["elapsed_seconds"],
                row["transport_error"],
            )

    client = SavedClient()
    result = run_cloud(
        root,
        paths,
        output,
        client=client,
        replay_origin=original,
        version=version,
        budget_cny=original["config"]["budget_cny"],
        previous_runs=[root / x["path"] for x in original.get("budget_lineage", [])],
        resume_from=(root / original["resume_origin"]["path"])
        if "resume_origin" in original
        else None,
        restart_stage=original.get("restart_stage"),
        document_id=original["selected_document_id"],
    )
    if result.get("budget_lineage", []) != original.get("budget_lineage", []):
        raise ValueError("Prior budget lineage changed")
    if (
        client.count != len(calls)
        or result["status"] != original["status"]
        or result["outputs_sha256"] != original["outputs_sha256"]
    ):
        raise ValueError("Cloud replay differs")
    result["outcome_and_artifact_equality_verified"] = True
    write_json(output / "report.json", result)
    return result
