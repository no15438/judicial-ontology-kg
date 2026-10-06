"""Current candidate workflow: run, replay, source retrieval, and reference evaluation."""

import argparse
import json
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

from judicial_kg.current.retrieval import graph_expand, index_documents, search
from judicial_kg.current.runner import run_document
from judicial_kg.extraction.bailian import BailianClient, credentials
from judicial_kg.extraction.cloud_cohort import SharedBudget
from judicial_kg.extraction.fresh_workflow import fingerprint
from judicial_kg.extraction.transport import Response
from judicial_kg.sources.development import safe_output, write_json
from judicial_kg.sources.records import canonical_hash, sha256


def replay(root, source_run, output):
    rows = json.loads((source_run / "calls.json").read_text())
    doc = json.loads((source_run / "source.json").read_text())

    class Saved:
        count = 0

        def call(self, body, timeout):
            row = rows[self.count]
            stage = row["stage"]
            if body != json.loads((source_run / (stage + ".request.json")).read_text()):
                raise RuntimeError("Replay request changed")
            self.count += 1
            return Response(
                row["http_status"],
                (source_run / (stage + ".response.bin")).read_bytes(),
                row["elapsed_seconds"],
                row["error"],
            )

    client = Saved()
    config = json.loads((source_run / "config.json").read_text())
    if fingerprint(root, []) != config["implementation"]:
        raise ValueError("Replay requires the frozen implementation")
    inventory = json.loads((source_run / "outputs.json").read_text())
    if any(
        sha256((source_run / name).read_bytes()) != digest for name, digest in inventory.items()
    ):
        raise ValueError("Saved artifact changed")
    run_document(root, doc, output, client, config["window_size"])
    for name in ("candidate", "graph", "coverage", "issues", "source-mappings"):
        if (source_run / (name + ".json")).read_bytes() != (output / (name + ".json")).read_bytes():
            raise ValueError("Replay outputs differ")
    if client.count != len(rows):
        raise ValueError("Unused historical responses")
    return {
        "offline_replay_verified": True,
        "network_calls": 0,
        "saved_requests_replayed": client.count,
    }


def run(root, manifest_path, output, credential_root):
    manifest = json.loads(manifest_path.read_text())
    documents = []
    for name, digest in manifest["input_hashes"].items():
        raw = (root / name).read_bytes()
        if sha256(raw) != digest:
            raise ValueError("Source changed")
        documents.extend(json.loads(raw))
    by_id = {d["document_id"]: d for d in documents}
    order = manifest["order"]
    if not order or len(order) != len(set(order)) or not set(order) <= by_id.keys():
        raise ValueError("Invalid selected documents")
    output = safe_output(root, output)
    output.mkdir(parents=True, exist_ok=False)
    frozen = fingerprint(root, [])
    write_json(output / "manifest.json", manifest)
    write_json(output / "frozen-inputs.json", frozen)
    # Persist every input document in the text index, including unselected graph documents.
    write_json(output / "text-index.json", index_documents(documents))
    budget = SharedBudget(
        BailianClient(*credentials(credential_root)),
        manifest["prior_committed_cny"],
        manifest["cumulative_cap_cny"],
        output / "budget.json",
        "qwen3.8-flash",
    )
    results = []

    def execute(i, identifier):
        if fingerprint(root, []) != frozen:
            raise ValueError("Implementation changed")
        dest = output / f"document-{i + 1:02}"
        r = run_document(root, by_id[identifier], dest, budget, manifest.get("window_size", 16))
        write_json(
            dest / "config.json",
            {"window_size": manifest.get("window_size", 16), "implementation": frozen},
        )
        write_json(
            dest / "outputs.json",
            {p.name: sha256(p.read_bytes()) for p in dest.iterdir() if p.is_file()},
        )
        r.update(replay(root, dest, output / f"replay-{i + 1:02}"))
        if fingerprint(root, []) != frozen:
            raise ValueError("Frozen implementation changed")
        return r

    report = {
        "status": "running",
        "input_documents": len(documents),
        "selected": len(order),
        "not_selected": len(documents) - len(order),
        "results": results,
        "old_full_document_acceptance_comparable": False,
        "semantic_precision_recall_f1": None,
    }
    write_json(output / "batch-report.json", report)
    with ThreadPoolExecutor(max_workers=manifest.get("concurrency", 3)) as pool:
        jobs = {
            pool.submit(execute, i, identifier): identifier for i, identifier in enumerate(order)
        }
        for job in as_completed(jobs):
            try:
                results.append(job.result())
            except Exception as error:
                results.append(
                    {
                        "document_id": jobs[job],
                        "status": "coordinator_failed",
                        "error": type(error).__name__,
                    }
                )
            write_json(output / "batch-report.json", report)
    report.update(status="completed", frozen_inputs_unchanged=fingerprint(root, []) == frozen)
    write_json(output / "batch-report.json", report)
    return report


def evaluate(rows, questions, records, k=10):
    """Upstream reference matching, not semantic gold or expert answer assessment."""
    result = []
    for q in questions:
        refs = q.get("relevant_document_ids", [])
        if not refs:
            result.append({"id": q["id"], "status": "unscored_missing_reference"})
            continue
        scores = {}
        for method in ("bm25", "tfidf", "hybrid", "graph_augmented"):
            hits = search(rows, q["query"], "hybrid" if method == "graph_augmented" else method, k)
            if method == "graph_augmented":
                hits = graph_expand(rows, hits, records)
            found = {h["document_id"] for h in hits}
            scores[method] = {
                "reference_document_recall": len(found & set(refs)) / len(set(refs)),
                "evidence_units_returned": len(hits),
                "returned_characters": sum(len(h["text"]) for h in hits),
            }
        result.append({"id": q["id"], "scores": scores})
    return {
        "questions": result,
        "reference_interpretation": "upstream_or_user_reference_not_graph_gold",
        "graph_expansion_uses_extra_context": True,
        "semantic_accuracy": None,
        "reference_sha256": canonical_hash(questions),
    }


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("command", choices=["run", "replay", "search", "evaluate"])
    p.add_argument("--manifest", type=Path)
    p.add_argument("--output", type=Path)
    p.add_argument("--credential-root", type=Path)
    p.add_argument("--source-run", type=Path)
    p.add_argument("--index", type=Path)
    p.add_argument("--query")
    p.add_argument("--questions", type=Path)
    p.add_argument("--candidate", action="append", type=Path, default=[])
    p.add_argument(
        "--method", choices=["bm25", "tfidf", "hybrid", "graph_augmented"], default="hybrid"
    )
    p.add_argument("--execute", action="store_true")
    args = p.parse_args()
    root = Path.cwd()
    if args.command == "run":
        if not all((args.execute, args.manifest, args.output, args.credential_root)):
            p.error("run requires --execute --manifest --output --credential-root")
        result = run(root, args.manifest, args.output, args.credential_root)
    elif args.command == "replay":
        if not args.source_run or not args.output:
            p.error("replay requires --source-run --output")
        result = replay(root, args.source_run, safe_output(root, args.output))
    else:
        if not args.index:
            p.error("--index required")
        rows = json.loads(args.index.read_text())
        records = [json.loads(f.read_text()) for f in args.candidate]
        if args.command == "evaluate":
            if not args.questions:
                p.error("--questions required")
            result = evaluate(rows, json.loads(args.questions.read_text()), records)
        else:
            if not args.query:
                p.error("--query required")
            result = search(
                rows, args.query, "hybrid" if args.method == "graph_augmented" else args.method
            )
            if args.method == "graph_augmented":
                result = graph_expand(rows, result, records)
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
