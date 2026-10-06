"""One frozen fresh invocation: source -> extraction -> offline replay -> candidate graph."""

import argparse
import json
import subprocess
from pathlib import Path

from judicial_kg.extraction.bailian_staged import replay_cloud, run_cloud
from judicial_kg.graph.run import run_assembly
from judicial_kg.sources.development import safe_output, write_json
from judicial_kg.sources.records import sha256


def fingerprint(root, paths):
    files = set(paths)
    for folder in ("src", "prompts", "ontology", "schemas", "configs"):
        files.update(p for p in (root / folder).rglob("*") if p.suffix in (".py", ".json", ".txt"))
    files.update(root / name for name in ("pyproject.toml", "uv.lock") if (root / name).exists())
    return {str(p.resolve()): sha256(p.read_bytes()) for p in sorted(files)}


def run_fresh(
    root,
    paths,
    output,
    *,
    version=31,
    previous_runs=(),
    budget_cny="200",
    client=None,
    document_id=None,
):
    """Prior reports are used only for cumulative billing; no old responses/candidates are read.

    Bounded corrections already built into the selected protocol are permitted. This
    controller never retries a failed run, edits a graph, or accepts an external repair.
    """
    root = root.resolve()
    output = safe_output(root, output)
    output.mkdir(parents=True, exist_ok=False)
    frozen = fingerprint(root, paths)
    write_json(output / "frozen-inputs.json", frozen)
    report = {
        "workflow": "fresh-source-to-graph-v1",
        "protocol_version": version,
        "code_commit": subprocess.check_output(
            ["git", "rev-parse", "HEAD"], cwd=root, text=True
        ).strip(),
        "completed": False,
        "status": "running",
        "response_reuse_enabled": False,
        "external_candidate_input": False,
        "automatic_pipeline_accepted": False,
        "uncorrected_first_pass_accepted": False,
        "semantic_precision_recall_f1": None,
    }

    def save():
        write_json(output / "workflow-report.json", report)

    def unchanged():
        if fingerprint(root, paths) != frozen:
            raise ValueError("Frozen code, prompt, contract, configuration or source changed")

    save()
    try:
        result = run_cloud(
            root,
            paths,
            output / "extraction",
            client=client,
            version=version,
            previous_runs=previous_runs,
            budget_cny=budget_cny,
            document_id=document_id,
        )
        report.update(
            extraction_status=result["status"],
            expected_documents=result["expected_documents"],
            selected_documents=result["selected_documents"],
            not_selected=result["not_selected"],
            requests_submitted=result["requests_submitted"],
            responses_reused=result.get("responses_reused", 0),
            model_correction_calls=result.get("model_correction_calls", 0),
            observed_cost_cny=result["observed_cost_cny"],
            prior_committed_cny=result.get("prior_committed_cny", "0"),
        )
        unchanged()
        if result.get("responses_reused", 0) or "resume_origin" in result:
            raise ValueError("Fresh workflow cannot reuse historical responses")
        replay = replay_cloud(root, paths, output / "extraction", output / "replay")
        if not replay["outcome_and_artifact_equality_verified"] or replay["requests_submitted"]:
            raise ValueError("Offline replay was not verified without new requests")
        report["offline_replay_verified"] = True
        if result["status"] != "accepted_candidate":
            report["status"] = "extraction_rejected"
        else:
            records = json.loads((output / "extraction/accepted.json").read_text())
            candidates = output / "candidates.jsonl"
            candidates.write_text(
                "".join(json.dumps(x, ensure_ascii=False) + "\n" for x in records)
            )
            unchanged()
            graph = run_assembly(
                root, paths, candidates, output / "graph", contract_version="0.3.0"
            )
            ok = graph["documents_assembled"] == 1 and graph["documents_assembly_failed"] == 0
            report.update(
                status="automatic_pipeline_accepted" if ok else "assembly_rejected",
                automatic_pipeline_accepted=ok,
                uncorrected_first_pass_accepted=ok and result.get("first_pass_accepted", False),
                graph={
                    k: graph[k]
                    for k in (
                        "nodes",
                        "edges",
                        "validation",
                        "diagnostic_hint_counts",
                        "assembled_documents_diagnostics",
                        "graph_canonical_sha256",
                    )
                },
            )
        unchanged()
        report["frozen_inputs_unchanged"] = True
    except (ValueError, KeyError, TypeError, OSError) as error:
        report.update(
            status="workflow_verification_failed",
            automatic_pipeline_accepted=False,
            uncorrected_first_pass_accepted=False,
            error_type=type(error).__name__,
        )
        # Error messages may contain private source; they stay in this ignored run directory.
        report["error"] = str(error)
    report["completed"] = True
    save()
    return report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=Path.cwd())
    parser.add_argument("--documents", type=Path, action="append", required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--document-id")
    parser.add_argument("--previous-run", type=Path, action="append", default=[])
    parser.add_argument("--version", type=int, default=31)
    parser.add_argument("--budget-cny", required=True)
    parser.add_argument("--execute", action="store_true")
    args = parser.parse_args()
    if not args.execute:
        parser.error("--execute is required to submit approved cloud requests")
    result = run_fresh(
        args.root,
        args.documents,
        args.output,
        version=args.version,
        previous_runs=args.previous_run,
        budget_cny=args.budget_cny,
        document_id=args.document_id,
    )
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return int(not result["automatic_pipeline_accepted"])


if __name__ == "__main__":
    raise SystemExit(main())
