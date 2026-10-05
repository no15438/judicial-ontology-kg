"""Reexecute saved v10 RPC receipts entirely offline, binding every staged request."""

from collections import Counter
from pathlib import Path

from judicial_kg.contracts import Contract
from judicial_kg.extraction import (
    actor_inventory,
    augmented_inventory,
    checked_forms,
    collective_mentions,
    compact_inventory,
    context_delta,
    context_first,
    context_ids,
    described_actors,
    evidence_first,
    fragment_repairs,
    grouped_mentions,
    identity_first,
    mention_stage,
    owner_first,
    scoped,
    scoped_incremental,
    space_inventory,
    span_first,
    typed_owner,
    value_stage,
)
from judicial_kg.extraction.run import _run_extraction
from judicial_kg.extraction.transport import Response
from judicial_kg.sources.development import write_json
from judicial_kg.sources.records import canonical_hash, sha256
from judicial_kg.validation_run import strict_json, validate_batch


def replay_completed(root, document_paths, source_run, output):
    root, source_run = root.resolve(), source_run.resolve()
    raw_report = (source_run / "report.json").read_bytes()
    original = strict_json(raw_report)
    if not original.get("completed") or original["config"]["version"] not in {
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
    }:
        raise ValueError("Staged replay requires a complete staged run")
    protocol = {
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
    }[original["config"]["version"]]
    # Verify every artifact before deriving output, including failed/unused phases.
    verified = {}
    for name, digest in original["outputs_sha256"].items():
        if Path(name).name != name:
            raise ValueError("Invalid saved artifact name")
        raw = (source_run / name).read_bytes()
        if sha256(raw) != digest:
            raise ValueError("Saved staged artifact hash mismatch")
        verified[name] = raw
    if sha256((source_run / "outcomes.json").read_bytes()) != original["outcomes_sha256"]:
        raise ValueError("Saved staged outcomes hash mismatch")
    docs, inputs = [], []
    for path in document_paths:
        raw = path.read_bytes()
        docs.extend(strict_json(raw))
        inputs.append({"sha256": sha256(raw), "bytes": len(raw)})
    if inputs != original["input_files"] or len(docs) != original["expected_documents"]:
        raise ValueError("Replay must retain the exact original input denominator")
    contract = Contract.load(root, protocol.CONTRACT_VERSION)
    if any(original.get(k) != v for k, v in contract.versions().items()):
        raise ValueError("Replay contract changed")
    validate_batch(docs, [], contract)
    config = strict_json(verified["config.json"])
    prompt = (root / protocol.PROMPT_FILE).read_bytes()
    if (
        config != original["config"]
        or canonical_hash(config) != original["config_sha256"]
        or prompt != verified["prompt.txt"]
        or sha256(prompt) != original["prompt_sha256"]
        or canonical_hash(protocol.draft_schema(contract)) != original["draft_schema_sha256"]
    ):
        raise ValueError("Replay configuration, prompt or schema changed")
    calls = original["response_metadata"]
    if (
        len([c for c in calls if c["path"] == "/v1/chat/completions"])
        != original["requests_submitted"]
    ):
        raise ValueError("Saved completion count differs")

    class SavedClient:
        position = 0

        def call(self, path, body=None, timeout=15):
            if self.position >= len(calls):
                raise RuntimeError("Replay requested an unsaved phase")
            call = calls[self.position]
            prefix = f"rpc-{call['rpc']:03}"
            request = strict_json(verified[prefix + ".request.json"])
            if request != {"path": path, "body": body} or path != call["path"]:
                # Do not let runner's protocol-failure handler disguise lineage failure.
                raise RuntimeError("Saved request differs from reconstructed staged request")
            raw = verified[prefix + ".response.bin"]
            if sha256(raw) != call["response_sha256"]:
                raise RuntimeError("Saved response receipt hash mismatch")
            self.position += 1
            return Response(
                call["status"],
                raw,
                call["elapsed_seconds"],
                call["error"],
                verified.get(prefix + ".diagnostics.bin", b""),
            )

    client = SavedClient()
    result = _run_extraction(
        root,
        document_paths,
        source_run / "config.json",
        output,
        client=client,
        replay_origin=original,
    )
    result["completed"] = False
    write_json(output / "report.json", result)
    if client.position != len(calls):
        raise RuntimeError("Replay did not consume every saved RPC")
    old_outcomes = strict_json((source_run / "outcomes.json").read_bytes())
    new_outcomes = strict_json((output / "outcomes.json").read_bytes())
    if (
        new_outcomes != old_outcomes
        or (output / "accepted.json").read_bytes() != verified["accepted.json"]
    ):
        raise RuntimeError("Staged replay did not reproduce original outcomes and candidates")
    for name, raw in verified.items():
        if name.startswith("stage-") or name == "selected-documents.json":
            if not (output / name).is_file() or (output / name).read_bytes() != raw:
                raise RuntimeError("Saved intermediate differs from reconstructed staged artifact")
    result.update(
        completed=True,
        run_kind="offline_staged_qwen_recompilation_not_new_inference",
        requests_submitted=0,
        saved_requests_replayed=original["requests_submitted"],
        source_run_id=original["run_id"],
        source_report_sha256=sha256(raw_report),
        original_run_unmodified=True,
        outcome_and_candidate_equality_verified=True,
        document_status_counts=dict(Counter(o["status"] for o in new_outcomes)),
    )
    write_json(output / "report.json", result)
    return result
