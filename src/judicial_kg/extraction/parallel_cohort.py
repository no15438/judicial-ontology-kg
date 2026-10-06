"""Document-parallel extraction under one exclusive repository coordinator."""

import argparse
import json
import os
import subprocess
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

from judicial_kg.extraction.fresh_workflow import fingerprint, run_fresh
from judicial_kg.extraction.run import busy, extraction_cohort, load_config
from judicial_kg.extraction.transport import SparkClient
from judicial_kg.sources.development import safe_output, write_json
from judicial_kg.sources.records import sha256


def run_parallel(root, manifest_path, output, *, client=None, runner=None):
    root = root.resolve()
    manifest = json.loads(manifest_path.read_text())
    paths = [root / p for p in manifest["input_hashes"]]
    for path, expected in zip(paths, manifest["input_hashes"].values(), strict=True):
        if sha256(path.read_bytes()) != expected:
            raise ValueError("Registered source changed")
    documents = [d for p in paths for d in json.loads(p.read_text())]
    order = manifest["order"]
    if (
        not order
        or len(order) != len(set(order))
        or not set(order) <= {d["document_id"] for d in documents}
    ):
        raise ValueError("Invalid cohort selection")
    config_path = root / manifest["local_config"]
    config = load_config(config_path)
    if (
        config["version"] != f"qwen-extraction-v{manifest['protocol_version']}"
        or config["max_documents"] != 1
    ):
        raise ValueError("Cohort protocol/config mismatch")
    limit = manifest["concurrency"]
    service = client or SparkClient(config["ssh_host"], config["port"])
    rows = [{"document_id": d, "status": "queued", "live_requests_submitted": 0} for d in order]
    retained_paths = []
    for prior in manifest.get("retained_runs", []):
        path = root / prior["workflow_report"]
        if sha256(path.read_bytes()) != prior["sha256"]:
            raise ValueError("Retained report changed")
        result = json.loads(path.read_text())
        identifier = result["selected_document_id"]
        if (
            identifier not in order
            or not result.get("completed")
            or not result.get("frozen_inputs_unchanged")
            or not result.get("offline_replay_verified")
        ):
            raise ValueError("Retained document is not a completed verified workflow")
        row = rows[order.index(identifier)]
        if row.get("retained_from_previous_batch"):
            raise ValueError("Duplicate retained document")
        row.update(result)
        row.update(
            retained_from_previous_batch=True, previous_report=str(path), document_id=identifier
        )
        retained_paths.append(path)
    output = safe_output(root, output)
    output.mkdir(parents=True, exist_ok=False)
    frozen_paths = [*paths, config_path, manifest_path, *retained_paths]
    frozen = fingerprint(root, frozen_paths)
    write_json(output / "frozen-inputs.json", frozen)
    lock = threading.RLock()
    started = time.time()
    report = {
        "status": "running",
        "completed": False,
        "run": manifest["run"],
        "pid": os.getpid(),
        "code_commit": subprocess.check_output(
            ["git", "rev-parse", "HEAD"], cwd=root, text=True
        ).strip(),
        "concurrency": limit,
        "expected_documents": len(documents),
        "selected_documents": len(order),
        "not_selected": len(documents) - len(order),
        "cloud_requests_submitted": 0,
        "historical_response_reuse": 0,
        "manual_graph_edits": 0,
        "semantic_precision_recall_f1": None,
        "retained_completed_documents": len(retained_paths),
        "retained_requests_submitted": sum(row.get("requests_submitted", 0) for row in rows),
        "results": rows,
        "observed_peak_processing": 0,
        "observed_peak_deferred": 0,
    }

    def save():
        with lock:
            report.update(
                elapsed_seconds=time.time() - started,
                finished_documents=sum(r["status"] not in {"queued", "running"} for r in rows),
                new_requests_submitted=sum(r["live_requests_submitted"] for r in rows),
                automatic_pipeline_accepted=sum(
                    r.get("automatic_pipeline_accepted", False) for r in rows
                ),
            )
            write_json(output / "batch-report.json", report)

    def unchanged():
        if fingerprint(root, frozen_paths) != frozen:
            raise ValueError("Frozen cohort changed")

    save()
    try:
        with extraction_cohort(root, limit) as lease:
            props = service.call("/props").json()
            if (props.get("total_slots") or 1) < limit or props["default_generation_settings"][
                "n_ctx"
            ] < config["context_tokens"]:
                raise ValueError("Service capacity below registered concurrency/context")
            if busy(service.call("/metrics").raw.decode()):
                raise ValueError("Service not idle at cohort start")

            def execute(index):
                row = rows[index]
                unchanged()
                with lock:
                    row.update(status="running", output_directory=f"document-{index + 1:02}")
                    save()

                class TrackedClient:
                    def call(self, path, body=None, timeout=15):
                        if path == "/v1/chat/completions":
                            with lock:
                                row["live_requests_submitted"] += 1
                                save()
                        response = service.call(path, body, timeout)
                        if path == "/metrics" and response.status == 200:
                            values = {}
                            for line in response.raw.decode().splitlines():
                                for key in ("processing", "deferred"):
                                    if line.startswith("llamacpp:requests_" + key + " "):
                                        values[key] = int(float(line.split()[-1]))
                            with lock:
                                for key, value in values.items():
                                    report["observed_peak_" + key] = max(
                                        report["observed_peak_" + key], value
                                    )
                        return response

                result = (runner or run_fresh)(
                    root,
                    paths,
                    output / row["output_directory"],
                    version=manifest["protocol_version"],
                    document_id=row["document_id"],
                    local_config=config_path,
                    client=TrackedClient(),
                    cohort_lease=lease,
                )
                unchanged()
                with lock:
                    row.update(result)
                    save()

            indexes = [i for i, row in enumerate(rows) if row["status"] == "queued"]
            try:
                with ThreadPoolExecutor(max_workers=limit) as pool:
                    futures = {pool.submit(execute, i): i for i in indexes}
                    for future in as_completed(futures):
                        try:
                            future.result()
                        except (ValueError, KeyError, TypeError, OSError, RuntimeError) as error:
                            with lock:
                                rows[futures[future]].update(
                                    status="coordinator_failed",
                                    error_type=type(error).__name__,
                                    error=str(error),
                                )
                                save()
                unchanged()
                report.update(status="completed", completed=True, frozen_inputs_unchanged=True)
            finally:
                save()
    except (ValueError, KeyError, TypeError, OSError, RuntimeError) as error:
        report.update(
            status="blocked", completed=False, error_type=type(error).__name__, error=str(error)
        )
        save()

    return report


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--manifest", type=Path, required=True)
    p.add_argument("--output", type=Path, required=True)
    p.add_argument("--execute", action="store_true")
    args = p.parse_args()
    if not args.execute:
        p.error("--execute is required")
    result = run_parallel(Path.cwd(), args.manifest, args.output)
    print(
        json.dumps({"status": result["status"], "finished_documents": result["finished_documents"]})
    )


if __name__ == "__main__":
    main()
