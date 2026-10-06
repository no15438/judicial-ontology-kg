"""Frozen serial local cohort with durable per-document states and no response reuse."""

import argparse
import json
import os
import subprocess
import time
from datetime import UTC, datetime
from pathlib import Path

from judicial_kg.extraction.fresh_workflow import fingerprint, run_fresh
from judicial_kg.extraction.run import busy, load_config
from judicial_kg.extraction.transport import SparkClient
from judicial_kg.sources.development import safe_output, write_json
from judicial_kg.sources.records import sha256


def run_batch(root, manifest_path, output, *, client=None, runner=None, readiness=None):
    root = root.resolve()
    manifest = json.loads(manifest_path.read_text())
    paths = [root / name for name in manifest["input_hashes"]]
    for path, expected in zip(paths, manifest["input_hashes"].values(), strict=True):
        if sha256(path.read_bytes()) != expected:
            raise ValueError("Registered source hash changed")
    documents = [d for path in paths for d in json.loads(path.read_text())]
    ids = [d["document_id"] for d in documents]
    selected = manifest["order"]
    if not selected or len(selected) != len(set(selected)) or len(ids) != len(set(ids)):
        raise ValueError("Cohort identifiers must be nonempty and unique")
    if not set(selected) <= set(ids):
        raise ValueError("Selected document missing from registered inputs")
    config_path = root / manifest["local_config"]
    config = load_config(config_path)
    if config["version"] != f"qwen-extraction-v{manifest['protocol_version']}":
        raise ValueError("Protocol differs from registration")
    if config["max_documents"] != 1:
        raise ValueError("Each fresh invocation must select exactly one document")
    output = safe_output(root, output)
    output.mkdir(parents=True, exist_ok=False)
    frozen_paths = [*paths, config_path, manifest_path]
    frozen = fingerprint(root, frozen_paths)
    write_json(output / "frozen-inputs.json", frozen)
    started = time.time()
    rows = [
        {"document_id": key, "status": "queued", "live_requests_submitted": 0} for key in selected
    ]
    report = {
        "run": manifest["run"],
        "code_commit": subprocess.check_output(
            ["git", "rev-parse", "HEAD"], cwd=root, text=True
        ).strip(),
        "pid": os.getpid(),
        "started_at_utc": datetime.now(UTC).isoformat(),
        "status": "running",
        "completed": False,
        "expected_documents": len(ids),
        "selected_documents": len(selected),
        "not_selected": len(ids) - len(selected),
        "backend": "spark_lan",
        "model": config["model"],
        "protocol_version": manifest["protocol_version"],
        "historical_response_reuse": 0,
        "manual_graph_edits": 0,
        "cloud_requests_submitted": 0,
        "semantic_precision_recall_f1": None,
        "results": rows,
    }

    def save():
        report.update(
            elapsed_seconds=time.time() - started,
            finished_documents=sum(row["status"] not in {"queued", "running"} for row in rows),
            requests_submitted=sum(row["live_requests_submitted"] for row in rows),
            automatic_pipeline_accepted=sum(
                row.get("automatic_pipeline_accepted", False) for row in rows
            ),
            uncorrected_first_pass_accepted=sum(
                row.get("uncorrected_first_pass_accepted", False) for row in rows
            ),
        )
        write_json(output / "batch-report.json", report)

    def unchanged():
        if fingerprint(root, frozen_paths) != frozen:
            raise ValueError("Frozen batch inputs changed")

    service = client or SparkClient(config["ssh_host"], config["port"])

    def ready():
        for _ in range(10):
            if service.call("/health").json().get("status") != "ok":
                raise ValueError("Local service unhealthy")
            if not busy(service.call("/metrics").raw.decode()):
                return
            report["service_state"] = "waiting_for_idle"
            save()
            time.sleep(30)
        raise ValueError("Local service remains busy")

    save()
    try:
        for index, row in enumerate(rows):
            unchanged()
            (readiness or ready)()
            report["service_state"] = "ready"
            row["status"] = "running"
            row["output_directory"] = f"document-{index + 1:02}"
            save()

            class TrackedClient:
                def __init__(self, target):
                    self.target = target

                def call(self, path, body=None, timeout=15):
                    if path == "/v1/chat/completions":
                        self.target["live_requests_submitted"] += 1
                        save()
                    return service.call(path, body, timeout)

            result = (runner or run_fresh)(
                root,
                paths,
                output / row["output_directory"],
                version=manifest["protocol_version"],
                document_id=row["document_id"],
                local_config=config_path,
                client=TrackedClient(row),
            )
            row.update(result)
            row["document_id"] = selected[index]
            unchanged()
            save()
        report["status"] = "completed"
        report["completed"] = True
        report["frozen_inputs_unchanged"] = True
    except (ValueError, KeyError, TypeError, OSError, RuntimeError) as error:
        report.update(status="blocked", error_type=type(error).__name__, error=str(error))
    finally:
        save()
    return report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=Path.cwd())
    parser.add_argument("--manifest", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--execute", action="store_true")
    args = parser.parse_args()
    if not args.execute:
        parser.error("--execute is required")
    result = run_batch(args.root, args.manifest, args.output)
    print(
        json.dumps({k: result[k] for k in ("status", "finished_documents", "requests_submitted")}),
        flush=True,
    )


if __name__ == "__main__":
    main()
