"""Serial, bounded local-Qwen development runs with an explicit outcome per source."""

import fcntl
import json
import subprocess
from collections import Counter
from contextlib import contextmanager
from datetime import UTC, datetime
from pathlib import Path
from uuid import uuid4

from judicial_kg.contracts import COLLECTIONS, Contract
from judicial_kg.extraction.draft import DraftError
from judicial_kg.extraction.transport import SparkClient
from judicial_kg.sources.development import safe_output, write_json
from judicial_kg.sources.records import canonical_hash, sha256
from judicial_kg.validation import validate_record, validate_source
from judicial_kg.validation_run import strict_json, validate_batch

PROMPT_FILE = "prompts/qwen-extraction-v3.txt"


def load_config(path: Path) -> dict:
    config = strict_json(path.read_bytes())
    integer_bounds = {
        "context_tokens": (1024, 32768),
        "max_output_tokens": (128, 16384),
        "token_margin": (64, 1024),
        "request_timeout_seconds": (10, 900),
        "seed": (0, 2**32 - 1),
        "limit_per_source": (1, 36),
        "max_documents": (1, 36),
    }
    required = set(integer_bounds) | {"version", "ssh_host", "port", "model", "temperature"}
    if not isinstance(config, dict) or set(config) != required:
        raise ValueError("Invalid extraction config keys")
    for key, (low, high) in integer_bounds.items():
        if type(config[key]) is not int or not low <= config[key] <= high:
            raise ValueError(f"Invalid extraction config {key}")
    if (
        config["version"]
        not in {
            "qwen-extraction-v3",
            "qwen-extraction-v4",
            "qwen-extraction-v5",
            "qwen-extraction-v6",
            "qwen-extraction-v7",
            "qwen-extraction-v8",
            "qwen-extraction-v9",
            "qwen-extraction-v10",
            "qwen-extraction-v11",
            "qwen-extraction-v12",
            "qwen-extraction-v13",
            "qwen-extraction-v14",
            "qwen-extraction-v15",
            "qwen-extraction-v16",
            "qwen-extraction-v17",
            "qwen-extraction-v18",
            "qwen-extraction-v19",
            "qwen-extraction-v20",
            "qwen-extraction-v21",
            "qwen-extraction-v22",
            "qwen-extraction-v23",
            "qwen-extraction-v24",
            "qwen-extraction-v25",
            "qwen-extraction-v26",
            "qwen-extraction-v27",
            "qwen-extraction-v28",
            "qwen-extraction-v29",
            "qwen-extraction-v30",
            "qwen-extraction-v31",
            "qwen-extraction-v32",
            "qwen-extraction-v33",
            "qwen-mention-repair-v1",
            "qwen-mention-repair-v2",
            "qwen-mention-repair-v3",
            "qwen-mention-repair-v4",
            "qwen-mention-repair-v5",
            "qwen-selector-repair-v1",
            "qwen-selector-repair-v2",
            "qwen-selector-repair-v3",
            "qwen-attribution-repair-v1",
            "qwen-local-review-v1",
            "qwen-local-review-v2",
            "qwen-actor-type-review-v1",
            "qwen-property-scope-review-v1",
            "qwen-context-scope-review-v1",
        }
        or not isinstance(config["model"], str)
        or not config["model"]
        or type(config["temperature"]) not in (int, float)
        or not 0 <= config["temperature"] <= 1
        or type(config["port"]) is not int
        or not isinstance(config["ssh_host"], str)
    ):
        raise ValueError("Unsupported model configuration")
    SparkClient(config["ssh_host"], config["port"])
    return config


def busy(metrics: str) -> bool:
    values = {}
    for line in metrics.splitlines():
        for name in ("llamacpp:requests_processing", "llamacpp:requests_deferred"):
            if line.startswith(name + " "):
                values[name] = float(line.split()[-1])
    if len(values) != 2:
        raise ValueError("Missing service load metrics")
    return any(value != 0 for value in values.values())


def completion_content(response: dict, model: str) -> tuple[str, dict]:
    if not isinstance(response, dict) or response.get("model") != model:
        raise DraftError("response_model_mismatch")
    choices = response.get("choices")
    if not isinstance(choices, list) or len(choices) != 1 or not isinstance(choices[0], dict):
        raise DraftError("response_choices_invalid")
    choice = choices[0]
    if choice.get("finish_reason") != "stop":
        raise DraftError(
            "response_truncated"
            if choice.get("finish_reason") == "length"
            else "response_not_stopped"
        )
    message = choice.get("message")
    if not isinstance(message, dict) or message.get("refusal") or message.get("tool_calls"):
        raise DraftError("response_refusal_or_tool_call")
    content = message.get("content")
    if not isinstance(content, str) or not content.strip():
        raise DraftError("response_content_empty")
    return content, usage_counts(response)


def usage_counts(response: dict) -> dict:
    usage = response.get("usage", {}) if isinstance(response, dict) else {}
    usage = usage if isinstance(usage, dict) else {}
    # Only numeric API usage goes to the metadata report; never copy free-form fields.
    numeric = {
        k: usage[k]
        for k in ("prompt_tokens", "completion_tokens", "total_tokens")
        if type(usage.get(k)) is int and usage[k] >= 0
    }
    return numeric


@contextmanager
def extraction_lock(root):
    """Serialize local runners in this checkout, including preflight and failure exits."""
    folder = root.resolve() / "local"
    folder.mkdir(parents=True, exist_ok=True)
    with (folder / "qwen-extraction.lock").open("a") as handle:
        try:
            fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            raise ValueError(
                "Another Qwen extraction run holds this repository's service lock"
            ) from None
        try:
            yield
        finally:
            fcntl.flock(handle, fcntl.LOCK_UN)


def run_extraction(root, document_paths, config_path, output, client=None, protocol_override=None):
    with extraction_lock(root):
        return _run_extraction(root, document_paths, config_path, output, client, protocol_override)


def _run_extraction(
    root: Path,
    document_paths: list[Path],
    config_path: Path,
    output: Path,
    client=None,
    protocol_override=None,
    replay_origin=None,
) -> dict:
    root = root.resolve()
    output = safe_output(root, output)
    config = load_config(config_path)
    from judicial_kg.extraction import (
        actor_grounded,
        actor_inventory,
        anchored,
        augmented_inventory,
        bounded_workflow,
        checked_forms,
        classified_inventory,
        collective_mentions,
        compact_inventory,
        constrained,
        context_delta,
        context_first,
        context_ids,
        contextual,
        described_actors,
        draft,
        evidence_first,
        fragment_repairs,
        grouped_mentions,
        identity_first,
        mention_stage,
        owner_first,
        preventive,
        scoped,
        scoped_incremental,
        space_inventory,
        span_first,
        typed,
        typed_owner,
        unique_forms,
        value_stage,
    )

    if (
        "-repair-" in config["version"] or "-review-" in config["version"]
    ) and protocol_override is None:
        raise ValueError("Repair requires verified immutable source-run inputs")
    protocol = (
        protocol_override
        or {
            "qwen-extraction-v3": draft,
            "qwen-extraction-v4": anchored,
            "qwen-extraction-v5": typed,
            "qwen-extraction-v6": contextual,
            "qwen-extraction-v7": actor_grounded,
            "qwen-extraction-v8": preventive,
            "qwen-extraction-v9": constrained,
            "qwen-extraction-v10": context_first,
            "qwen-extraction-v11": identity_first,
            "qwen-extraction-v12": scoped,
            "qwen-extraction-v13": scoped_incremental,
            "qwen-extraction-v14": span_first,
            "qwen-extraction-v15": actor_inventory,
            "qwen-extraction-v16": compact_inventory,
            "qwen-extraction-v17": space_inventory,
            "qwen-extraction-v18": context_delta,
            "qwen-extraction-v19": context_ids,
            "qwen-extraction-v20": mention_stage,
            "qwen-extraction-v21": value_stage,
            "qwen-extraction-v22": evidence_first,
            "qwen-extraction-v23": owner_first,
            "qwen-extraction-v24": typed_owner,
            "qwen-extraction-v25": collective_mentions,
            "qwen-extraction-v26": grouped_mentions,
            "qwen-extraction-v27": checked_forms,
            "qwen-extraction-v28": fragment_repairs,
            "qwen-extraction-v29": described_actors,
            "qwen-extraction-v30": augmented_inventory,
            "qwen-extraction-v31": unique_forms,
            "qwen-extraction-v32": bounded_workflow,
            "qwen-extraction-v33": classified_inventory,
        }[config["version"]]
    )
    contract = Contract.load(root, getattr(protocol, "CONTRACT_VERSION", "0.1.0"))
    prompt_file = getattr(protocol, "PROMPT_FILE", PROMPT_FILE)
    prompt = (root / prompt_file).read_text()
    schema = protocol.draft_schema(contract)
    documents, inputs = [], []
    for path in document_paths:
        raw = path.read_bytes()
        docs = strict_json(raw)
        if not isinstance(docs, list):
            raise ValueError("Expected document arrays")
        documents.extend(docs)
        inputs.append({"sha256": sha256(raw), "bytes": len(raw)})
    # Validate the complete denominator before any model call or output creation.
    validate_batch(documents, [], contract)
    for doc in documents:
        validate_source(doc)
    selected, per_source = [], Counter()
    wanted = getattr(protocol_override, "selection_ids", None)
    if wanted is None:
        indices = range(len(documents))
    else:
        by_id = {d["document_id"]: i for i, d in enumerate(documents)}
        if len(set(wanted)) != len(wanted) or any(key not in by_id for key in wanted):
            raise ValueError("Repair target IDs must be unique and present in the full inputs")
        indices = [by_id[key] for key in wanted]
    for index in indices:
        doc = documents[index]
        if (
            len(selected) < config["max_documents"]
            and per_source[doc["dataset"]] < config["limit_per_source"]
        ):
            selected.append(index)
            per_source[doc["dataset"]] += 1
    if protocol_override is not None:
        protocol.check_selection([documents[i] for i in selected], config)
    client = client or SparkClient(config["ssh_host"], config["port"])
    # Never overwrite an earlier trial, including a failed or interrupted trial.
    output.mkdir(parents=True, exist_ok=False)
    if protocol_override is not None:
        protocol.save_plan(output)
    run_id = replay_origin["run_id"] if replay_origin else uuid4().hex
    outcomes = [
        {
            "document_id": d["document_id"],
            "selected": i in selected,
            "status": "pending" if i in selected else "not_selected",
            "issues": [],
        }
        for i, d in enumerate(documents)
    ]
    records, responses, rpc_number = [], [], 0
    metadata = {
        "schema_version": 1,
        "run_id": run_id,
        "run_kind": "local_qwen_development_extraction_not_gold",
        "started_at_utc": datetime.now(UTC).isoformat(),
        "completed": False,
        "code_commit": subprocess.check_output(
            ["git", "rev-parse", "HEAD"], cwd=root, text=True
        ).strip(),
        "worktree_dirty": bool(subprocess.check_output(["git", "status", "--porcelain"], cwd=root)),
        "code_sha256": {
            str(p.relative_to(root)): sha256(p.read_bytes())
            for p in sorted((root / "src/judicial_kg").rglob("*.py"))
        },
        "dependency_lock_sha256": sha256((root / "uv.lock").read_bytes()),
        "config": config,
        "config_sha256": canonical_hash(config),
        "prompt_sha256": sha256(prompt.encode()),
        "prompt_file": prompt_file,
        "draft_schema_sha256": canonical_hash(schema),
        "value_normalizer_version": getattr(protocol, "NORMALIZER_VERSION", None),
        **contract.versions(),
        "input_files": inputs,
        "expected_documents": len(documents),
        "selected_documents": len(selected),
        "requests_submitted": 0,
        "serial_service_guard": "repository_process_lock",
        "model_weight_hash_verified": False,
        "gold_available": False,
        "semantic_precision_recall_f1": None,
        "requires_user_legal_annotation": False,
    }
    if hasattr(protocol, "GENERATION_CONSTRAINT_VERSION"):
        metadata["generation_constraints"] = {
            "version": protocol.GENERATION_CONSTRAINT_VERSION,
            "scope": getattr(
                protocol,
                "GENERATION_CONSTRAINT_SCOPE",
                "structural_schema_enums_and_source_unit_ids",
            ),
            "local_only": ["uniqueness", "predicate_conditions", "references", "source_grounding"],
            "full_contract_validation_required": True,
            "unconstrained_fallback": False,
        }
    if protocol_override is not None:
        metadata["run_kind"] = getattr(
            protocol, "RUN_KIND", "local_qwen_bounded_mention_repair_not_gold"
        )
        metadata["repair_lineage"] = protocol.lineage
    write_json(output / "selected-documents.json", [documents[i] for i in selected])
    write_json(output / "config.json", config)
    (output / "prompt.txt").write_text(prompt)

    def checkpoint():
        if protocol_override is not None:
            metadata["repair_decision_counts"] = protocol.decision_counts
        write_json(output / "outcomes.json", outcomes)
        metadata["outcomes_sha256"] = sha256((output / "outcomes.json").read_bytes())
        metadata["selected_outcomes"] = [o.copy() for o in outcomes if o["selected"]]
        metadata["document_status_counts"] = dict(
            sorted(Counter(o["status"] for o in outcomes).items())
        )
        metadata["response_metadata"] = responses
        write_json(output / "report.json", metadata)

    def call(path, body=None, timeout=15):
        nonlocal rpc_number
        rpc_number += 1
        prefix = f"rpc-{rpc_number:03}"
        write_json(output / f"{prefix}.request.json", {"path": path, "body": body})
        response = client.call(path, body, timeout)
        (output / f"{prefix}.response.bin").write_bytes(response.raw)
        if response.diagnostics:
            (output / f"{prefix}.diagnostics.bin").write_bytes(response.diagnostics)
        responses.append(
            {
                "rpc": rpc_number,
                "path": path,
                "status": response.status,
                "error": response.error,
                "elapsed_seconds": response.elapsed_seconds,
                "response_sha256": sha256(response.raw),
            }
        )
        if response.error or response.status != 200:
            raise DraftError("transport_failed" if response.error else "http_failed")
        return response

    checkpoint()
    try:
        if call("/health").json().get("status") != "ok":
            raise DraftError("service_unhealthy")
        model_info = call("/v1/models").json()
        props = call("/props").json()
        if config["model"] not in [m.get("id") for m in model_info.get("data", [])]:
            raise DraftError("configured_model_unavailable")
        actual_context = props["default_generation_settings"]["n_ctx"]
        if type(actual_context) is not int or actual_context < config["context_tokens"]:
            raise DraftError("service_context_mismatch")
        metadata["service_snapshot"] = {
            "models_sha256": canonical_hash(model_info),
            "props_sha256": canonical_hash(props),
            "context_tokens": actual_context,
            "slots": props.get("total_slots"),
            "build_info": props.get("build_info"),
        }
    except (DraftError, ValueError, KeyError, TypeError, AttributeError) as error:
        code = error.code if isinstance(error, DraftError) else "preflight_invalid_response"
        for i in selected:
            outcomes[i].update(status="preflight_failed", issues=[{"code": code}])
    else:
        halted = False
        for index in selected:
            outcome, doc = outcomes[index], documents[index]
            if halted:
                outcome["status"] = "not_attempted_after_transport_failure"
                continue
            checkpoint()
            try:

                def infer(stage_messages, stage_format, stage_key=None, outcome=outcome):
                    receipt = {"stage": stage_key, "status": "pending"}
                    if stage_key is not None:
                        outcome.setdefault("stages", []).append(receipt)
                    try:
                        if busy(call("/metrics").raw.decode()):
                            raise DraftError("service_busy")
                        request = {
                            "model": config["model"],
                            "messages": stage_messages,
                            "max_tokens": config["max_output_tokens"],
                            "temperature": config["temperature"],
                            "seed": config["seed"],
                            "stream": False,
                            "cache_prompt": False,
                            "chat_template_kwargs": {"enable_thinking": False},
                            "response_format": stage_format,
                        }
                        if stage_key is not None:
                            receipt["messages_sha256"] = canonical_hash(stage_messages)
                            receipt["response_format_sha256"] = canonical_hash(stage_format)
                        if "schema" in stage_format:
                            outcome["response_format_sha256"] = canonical_hash(stage_format)
                        template = call(
                            "/apply-template",
                            {
                                "messages": request["messages"],
                                "chat_template_kwargs": request["chat_template_kwargs"],
                            },
                        ).json()
                        tokenized = call(
                            "/tokenize",
                            {
                                "content": template["prompt"],
                                "add_special": False,
                                "parse_special": True,
                            },
                        ).json()
                        tokens = tokenized.get("tokens")
                        if (
                            not isinstance(tokens, list)
                            or not tokens
                            or any(type(t) is not int for t in tokens)
                        ):
                            raise DraftError("tokenizer_invalid_response")
                        outcome["input_tokens"] = len(tokens)
                        if stage_key is not None:
                            receipt["input_tokens"] = len(tokens)
                        if (
                            len(tokens) + config["max_output_tokens"] + config["token_margin"]
                            > config["context_tokens"]
                        ):
                            raise DraftError("context_over_budget")
                        outcome["status"] = "in_flight"
                        metadata["requests_submitted"] += 1
                        checkpoint()
                        if stage_key is not None:
                            receipt["completion_rpc"] = rpc_number + 1
                        response = call(
                            "/v1/chat/completions", request, config["request_timeout_seconds"]
                        )
                        outcome["request_elapsed_seconds"] = (
                            outcome.get("request_elapsed_seconds", 0) + response.elapsed_seconds
                            if stage_key is not None
                            else response.elapsed_seconds
                        )
                        if stage_key is not None:
                            receipt["request_elapsed_seconds"] = response.elapsed_seconds
                        payload = response.json()
                        outcome["usage"] = usage_counts(payload)
                        if stage_key is not None:
                            receipt["usage"] = usage_counts(payload)
                        content, _ = completion_content(payload, config["model"])
                        receipt["status"] = "response_complete"
                        return content
                    except (DraftError, ValueError, KeyError, TypeError, AttributeError) as error:
                        receipt["status"] = (
                            error.code
                            if isinstance(error, DraftError)
                            else "protocol_invalid_response"
                        )
                        raise
                    finally:
                        checkpoint()

                provenance = {
                    "method": "model",
                    "producer": config["model"],
                    "version": metadata["service_snapshot"]["models_sha256"],
                    "run_id": run_id,
                }
                if config["version"] in {
                    "qwen-extraction-v10",
                    "qwen-extraction-v11",
                    "qwen-extraction-v12",
                    "qwen-extraction-v13",
                    "qwen-extraction-v14",
                    "qwen-extraction-v15",
                    "qwen-extraction-v16",
                    "qwen-extraction-v17",
                    "qwen-extraction-v18",
                    "qwen-extraction-v19",
                    "qwen-extraction-v20",
                    "qwen-extraction-v21",
                    "qwen-extraction-v22",
                    "qwen-extraction-v23",
                    "qwen-extraction-v24",
                    "qwen-extraction-v25",
                    "qwen-extraction-v26",
                    "qwen-extraction-v27",
                    "qwen-extraction-v28",
                    "qwen-extraction-v29",
                    "qwen-extraction-v30",
                    "qwen-extraction-v31",
                    "qwen-extraction-v32",
                    "qwen-extraction-v33",
                }:

                    def save_stage(name, value, index=index):
                        write_json(output / f"stage-{index:03}-{name}.json", value)

                    record = protocol.extract(doc, prompt, contract, provenance, infer, save_stage)
                    outcome["input_tokens"] = sum(
                        s.get("input_tokens", 0) for s in outcome["stages"]
                    )
                    outcome["usage"] = {
                        k: sum(s.get("usage", {}).get(k, 0) for s in outcome["stages"])
                        for k in ("prompt_tokens", "completion_tokens", "total_tokens")
                    }
                else:
                    fmt = (
                        protocol.response_format(doc, schema)
                        if hasattr(protocol, "response_format")
                        else {"type": "json_object"}
                    )
                    content = infer(protocol.messages(doc, prompt, schema), fmt)
                    record = protocol.materialize(content, doc, contract, provenance)
                issues = validate_record(record, doc, contract)
                write_json(output / f"candidate-{index:03}.json", record)
                records.append(record)
                outcome["issues"] = issues
                if issues:
                    outcome["status"] = "contract_rejected"
                else:
                    outcome["status"] = (
                        "accepted_candidate"
                        if any(record[k] for k in COLLECTIONS)
                        else "accepted_empty"
                    )
            except (DraftError, ValueError, KeyError, TypeError, AttributeError) as error:
                code = error.code if isinstance(error, DraftError) else "protocol_invalid_response"
                outcome.update(
                    status=code,
                    issues=getattr(
                        error, "issues", [{"code": code, "path": getattr(error, "path", "/")}]
                    ),
                )
                halted = (
                    code == "transport_failed"
                )  # An uncertain timeout may leave a request in flight.
            if "stages" in outcome:
                outcome["input_tokens"] = sum(s.get("input_tokens", 0) for s in outcome["stages"])
                outcome["usage"] = {
                    k: sum(s.get("usage", {}).get(k, 0) for s in outcome["stages"])
                    for k in ("prompt_tokens", "completion_tokens", "total_tokens")
                }
            checkpoint()
    lines = [json.dumps(r, ensure_ascii=False) for r in records]
    (output / "candidates.jsonl").write_text("".join(line + "\n" for line in lines))
    validation = validate_batch(documents, lines, contract)
    write_json(output / "accepted.json", validation["accepted_records"])
    write_json(
        output / "validation-ledger.json",
        {k: validation[k] for k in ("document_outcomes", "attempts")},
    )
    metadata["validation"] = validation["summary"]
    metadata["outputs_sha256"] = {
        p.name: sha256(p.read_bytes())
        for p in sorted(output.iterdir())
        if p.is_file() and p.name not in ("report.json", "outcomes.json")
    }
    metadata["completed"] = True
    metadata["finished_at_utc"] = datetime.now(UTC).isoformat()
    checkpoint()
    return metadata
