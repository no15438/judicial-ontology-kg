"""Fresh v40 cloud cohort with a shared, durable concurrent spending guard."""

import argparse
import json
import os
import threading
from concurrent.futures import ThreadPoolExecutor, as_completed
from decimal import Decimal
from pathlib import Path

from judicial_kg.extraction.bailian import MODEL, BailianClient, credentials
from judicial_kg.extraction.bailian_staged import cost
from judicial_kg.extraction.fresh_workflow import fingerprint, run_fresh
from judicial_kg.extraction.run import extraction_cohort, usage_counts
from judicial_kg.extraction.transport import Response
from judicial_kg.sources.development import safe_output, write_json
from judicial_kg.sources.records import sha256


class SharedBudget:
    def __init__(self, client, prior, cap, ledger, model=MODEL):
        self.model = model
        self.client, self.ledger = client, ledger
        self.prior, self.cap = Decimal(prior), Decimal(cap)
        if not 0 <= self.prior < self.cap <= 200:
            raise ValueError("Invalid cumulative authorization")
        self.lock = threading.RLock()
        self.committed = Decimal(0)
        self.observed = Decimal(0)
        self.calls = 0
        self.blocked = 0
        self.halted = False
        self.save()

    def save(self):
        write_json(
            self.ledger,
            {
                "prior_committed_cny": str(self.prior),
                "cap_cny": str(self.cap),
                "new_committed_cny": str(self.committed),
                "new_observed_cny": str(self.observed),
                "cumulative_committed_cny": str(self.prior + self.committed),
                "actual_api_requests": self.calls,
                "budget_blocked_calls": self.blocked,
                "halted": self.halted,
                "note": "List-price estimate, not invoice; unknown usage retains reservation",
            },
        )

    def call(self, body, timeout):
        if body["model"] != self.model:
            raise ValueError("Model differs from budget registration")
        upper = sum(len(m["content"].encode()) for m in body["messages"]) + 4096
        reserve = cost(upper, body["max_tokens"], self.model)
        with self.lock:
            if self.halted or self.prior + self.committed + reserve > self.cap:
                self.blocked += 1
                self.save()
                return Response(None, b"", 0, "shared_budget_exhausted")
            self.committed += reserve
            self.calls += 1
            self.save()
        response = self.client.call(body, timeout)
        with self.lock:
            if response.status in {401, 403, 404}:
                self.halted = True
            if response.status == 200 and not response.error:
                try:
                    usage = usage_counts(response.json())
                    actual = cost(usage["prompt_tokens"], usage["completion_tokens"], self.model)
                    self.observed += actual
                    if (
                        usage["prompt_tokens"] <= upper
                        and usage["completion_tokens"] <= body["max_tokens"]
                    ):
                        self.committed += actual - reserve
                    else:
                        self.committed += max(Decimal(0), actual - reserve)
                        self.halted = True
                except (ValueError, KeyError, TypeError):
                    self.halted = True
            self.save()
        return response


def run(root, manifest_path, output, credential_root):
    manifest = json.loads(manifest_path.read_text())
    paths = [root / p for p in manifest["input_hashes"]]
    for p, digest in zip(paths, manifest["input_hashes"].values(), strict=True):
        if sha256(p.read_bytes()) != digest:
            raise ValueError("Source changed")
    docs = [d for p in paths for d in json.loads(p.read_text())]
    order = manifest["order"]
    if (
        not 1 <= len(order) <= 20
        or len(set(order)) != len(order)
        or not set(order) <= {d["document_id"] for d in docs}
    ):
        raise ValueError("Expected one to twenty unique registered documents")
    client = BailianClient(*credentials(credential_root))
    output = safe_output(root, output)
    output.mkdir(parents=True, exist_ok=False)
    budget = SharedBudget(
        client,
        manifest["prior_committed_cny"],
        manifest.get("cumulative_cap_cny", "200"),
        output / "budget.json",
        manifest.get("model", MODEL),
    )
    frozen = fingerprint(root, [*paths, manifest_path])
    write_json(output / "frozen-inputs.json", frozen)
    rows = [{"document_id": d, "status": "queued"} for d in order]
    report = {
        "status": "running",
        "pid": os.getpid(),
        "model": manifest.get("model", MODEL),
        "protocol_version": manifest["protocol_version"],
        "concurrency": manifest["concurrency"],
        "expected_documents": len(docs),
        "selected_documents": len(order),
        "not_selected": len(docs) - len(order),
        "results": rows,
        "historical_response_reuse": 0,
        "manual_graph_edits": 0,
        "semantic_precision_recall_f1": None,
    }
    lock = threading.RLock()

    def save():
        with lock:
            report["finished_documents"] = sum(
                r["status"] not in {"running", "queued"} for r in rows
            )
            report["automatic_pipeline_accepted"] = sum(
                r.get("automatic_pipeline_accepted", False) for r in rows
            )
            write_json(output / "batch-report.json", report)

    save()
    with extraction_cohort(root, manifest["concurrency"]) as lease:

        def execute(i):
            if fingerprint(root, [*paths, manifest_path]) != frozen:
                raise ValueError("Frozen input changed")
            with lock:
                rows[i]["status"] = "running"
                save()
            result = run_fresh(
                root,
                paths,
                output / f"document-{i + 1:02}",
                version=manifest["protocol_version"],
                cloud_model=manifest.get("model", MODEL),
                document_id=order[i],
                client=budget,
                budget_cny=str(budget.cap - budget.prior),
                cohort_lease=lease,
            )
            with lock:
                rows[i].update(result)
                save()

        with ThreadPoolExecutor(max_workers=manifest["concurrency"]) as pool:
            jobs = {pool.submit(execute, i): i for i in range(len(order))}
            for job in as_completed(jobs):
                try:
                    job.result()
                except Exception as error:
                    with lock:
                        rows[jobs[job]].update(
                            status="coordinator_failed", error_type=type(error).__name__
                        )
                        save()
    report["status"] = "completed"
    report["frozen_inputs_unchanged"] = fingerprint(root, [*paths, manifest_path]) == frozen
    save()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--manifest", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--credential-root", required=True, type=Path)
    parser.add_argument("--execute", action="store_true")
    args = parser.parse_args()
    if not args.execute:
        parser.error("--execute required")
    run(Path.cwd(), args.manifest, args.output, args.credential_root)


if __name__ == "__main__":
    main()
