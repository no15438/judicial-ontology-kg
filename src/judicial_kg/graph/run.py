"""Source validation, lossless assembly, denominator accounting and local export."""

import subprocess
import sys
from collections import Counter
from datetime import UTC, datetime
from pathlib import Path

from judicial_kg.contracts import COLLECTIONS, Contract
from judicial_kg.graph.assembly import GRAPH_VERSION, empty_graph, project_records, restore_records
from judicial_kg.graph.diagnostics import DIAGNOSTIC_VERSION, diagnose_record
from judicial_kg.sources.development import safe_output, write_json
from judicial_kg.sources.records import canonical_hash, sha256
from judicial_kg.validation_run import strict_json, validate_batch


def assemble_batch(documents: list[dict], lines: list[str], contract: Contract) -> dict:
    validation = validate_batch(documents, lines, contract)
    documents_by_id = {doc["document_id"]: doc for doc in documents}
    graph, diagnostics, outcomes = empty_graph(), [], []
    assembled = {}
    for record in validation["accepted_records"]:
        doc_id = record["document_id"]
        outcome = {"document_id": doc_id, "status": "assembly_failed", "issues": []}
        assembled[doc_id] = outcome
        try:
            candidate_graph = project_records([record])
            restored = restore_records(candidate_graph)
            if canonical_hash(restored) != canonical_hash([record]):
                raise ValueError("Assembly lost or changed candidate content")
            diagnostic = diagnose_record(record, documents_by_id[doc_id])
        except (ValueError, KeyError, TypeError, IndexError):
            outcome["issues"] = [{"code": "assembly_integrity_failed", "path": "/"}]
            continue
        outcome.update(
            status="assembled_candidate"
            if any(record[k] for k in COLLECTIONS)
            else "assembled_empty",
            input_record_sha256=canonical_hash(record),
            restored_record_sha256=canonical_hash(restored[0]),
            lossless_roundtrip=True,
        )
        graph["nodes"].extend(candidate_graph["nodes"])
        graph["edges"].extend(candidate_graph["edges"])
        diagnostics.append(diagnostic)
    graph["nodes"].sort(key=lambda n: n["id"])
    graph["edges"].sort(key=lambda e: e["id"])
    # Also check the combined graph: independent document graphs cannot collide.
    restore_records(graph)
    for original in validation["document_outcomes"]:
        outcomes.append(
            {
                **original,
                "validation_status": original["status"],
                **assembled.get(original["document_id"], {}),
            }
        )
    counts = Counter(o["status"] for o in outcomes)
    coverage = Counter()
    hints = Counter()
    for diagnostic in diagnostics:
        summary = diagnostic["summary"]
        coverage.update({k: v for k, v in summary.items() if type(v) is int})
        hints.update(summary["hint_counts"])
    return {
        "graph": graph,
        "diagnostics": diagnostics,
        "ledger": {"document_outcomes": outcomes, "attempts": validation["attempts"]},
        "summary": {
            "validation": validation["summary"],
            "document_status_counts": dict(sorted(counts.items())),
            "documents_assembled": counts["assembled_candidate"] + counts["assembled_empty"],
            "documents_assembly_failed": counts["assembly_failed"],
            "nodes": len(graph["nodes"]),
            "edges": len(graph["edges"]),
            "node_counts": dict(sorted(Counter(n["kind"] for n in graph["nodes"]).items())),
            "edge_counts": dict(sorted(Counter(e["relation"] for e in graph["edges"]).items())),
            "assembled_documents_diagnostics": dict(sorted(coverage.items())),
            "diagnostic_hint_counts": dict(sorted(hints.items())),
            "graph_canonical_sha256": canonical_hash(graph),
            "cross_document_identity_merges": 0,
            "semantic_precision_recall_f1": None,
            "gold_available": False,
            "requires_user_legal_annotation": False,
            "semantic_completeness": "not_assessed",
        },
    }


def run_assembly(
    root: Path,
    document_paths: list[Path],
    candidates: Path,
    output: Path,
    contract_version: str = "0.1.0",
) -> dict:
    root = root.resolve()
    output = safe_output(root, output)
    if output.exists():
        raise ValueError("Graph export requires a new output directory; prior runs are immutable")
    contract = Contract.load(root, contract_version)
    documents, inputs = [], []
    for path in document_paths:
        raw = path.read_bytes()
        value = strict_json(raw)
        if not isinstance(value, list):
            raise ValueError("Expected a JSON array of source documents")
        documents.extend(value)
        inputs.append({"kind": "documents", "sha256": sha256(raw), "bytes": len(raw)})
    raw = candidates.read_bytes()
    inputs.append({"kind": "candidates", "sha256": sha256(raw), "bytes": len(raw)})
    result = assemble_batch(documents, raw.decode("utf-8").splitlines(), contract)
    output.mkdir(parents=True, exist_ok=False)
    for name in ("graph", "diagnostics", "ledger"):
        write_json(output / f"{name}.json", result[name])
    report = {
        "schema_version": 1,
        "run_kind": "offline_candidate_graph_assembly_not_semantic_evaluation",
        "completed": True,
        "observed_at_utc": datetime.now(UTC).isoformat(),
        "graph_version": GRAPH_VERSION,
        "diagnostic_version": DIAGNOSTIC_VERSION,
        **contract.versions(),
        "code_commit": subprocess.check_output(
            ["git", "rev-parse", "HEAD"], cwd=root, text=True
        ).strip(),
        "worktree_dirty": bool(subprocess.check_output(["git", "status", "--porcelain"], cwd=root)),
        "code_sha256": {
            str(p.relative_to(root)): sha256(p.read_bytes())
            for p in sorted((root / "src/judicial_kg").rglob("*.py"))
        },
        "runtime": {
            "python": sys.version.split()[0],
            "dependency_lock_sha256": sha256((root / "uv.lock").read_bytes()),
        },
        "inputs": inputs,
        "outputs": {p.name: sha256(p.read_bytes()) for p in sorted(output.iterdir())},
        "requests_submitted": 0,
        **result["summary"],
    }
    write_json(output / "report.json", report)
    return report
