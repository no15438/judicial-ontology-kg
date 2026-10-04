"""Summarize accepted candidate deltas; counts are not semantic quality scores."""

import argparse
import json
from collections import Counter
from pathlib import Path

from judicial_kg.contracts import Contract
from judicial_kg.graph.diagnostics import diagnose_record
from judicial_kg.sources.development import safe_output, write_json
from judicial_kg.sources.records import sha256
from judicial_kg.validation_run import strict_json, validate_batch


def attribution(context):
    if "attributed_actor_ids" in context:
        return context["attributed_actor_ids"]
    actor_id = context.get("attributed_actor_id")
    return [] if actor_id is None else [actor_id]


parser = argparse.ArgumentParser(description=__doc__)
parser.add_argument("--before", type=Path, required=True)
parser.add_argument("--after", type=Path, required=True)
parser.add_argument("--documents", type=Path, action="append", required=True)
parser.add_argument("--output", type=Path, required=True)
args = parser.parse_args()
root = Path(__file__).resolve().parents[1]
output = safe_output(root, args.output)
if output.exists():
    raise ValueError("Comparison output must be new")
documents = [d for p in args.documents for d in json.loads(p.read_text())]
by_id = {d["document_id"]: d for d in documents}
runs = {}
accepted = {}
for label, path, version in [("before", args.before, "0.1.0"), ("after", args.after, "0.2.0")]:
    raw = path.read_bytes()
    run_report_raw = (path.parent / "report.json").read_bytes()
    run_report = strict_json(run_report_raw)
    if not run_report["completed"]:
        raise ValueError("Cannot compare an unfinished run")
    if run_report["outputs_sha256"][path.name] != sha256(raw):
        raise ValueError("Candidates do not match their saved run report")
    result = validate_batch(documents, raw.decode().splitlines(), Contract.load(root, version))
    accepted[label] = {r["document_id"]: r for r in result["accepted_records"]}
    rows = []
    for record in result["accepted_records"]:
        diagnostic = diagnose_record(record, by_id[record["document_id"]])
        rows.append(
            {
                "document_id": record["document_id"],
                "counts": {
                    k: len(record[k])
                    for k in ("actors", "mentions", "contexts", "roles", "events", "statements")
                },
                "multiple_speaker_contexts": sum(
                    len(attribution(c)) > 1 for c in record["contexts"]
                ),
                "nested_contexts": sum(
                    c["parent_context_id"] is not None for c in record["contexts"]
                ),
                "context_kind_counts": dict(Counter(c["kind"] for c in record["contexts"])),
                "literal_occurrences_without_mention": diagnostic["summary"][
                    "literal_name_occurrences_without_mention"
                ],
                "hints": diagnostic["summary"]["hint_counts"],
            }
        )
    runs[label] = {
        "candidate_sha256": sha256(raw),
        "source_run_report_sha256": sha256(run_report_raw),
        "source_run_status_counts": run_report["document_status_counts"],
        "source_run_selected_outcomes": run_report["selected_outcomes"],
        "validation": result["summary"],
        "documents": rows,
    }
fixed_literal_comparison = []
for document_id, record in accepted["before"].items():
    baseline_hints = diagnose_record(record, by_id[document_id])[
        "literal_name_occurrences_without_mention"
    ]
    locations = {(x["field"], x["start"], x["end"]) for x in baseline_hints}
    current = accepted["after"].get(document_id)
    matched = None
    old_mentions_without_overlap = None
    old_locations = {
        (m["evidence"]["field"], m["evidence"]["start"], m["evidence"]["end"])
        for m in record["mentions"]
    }
    if current is not None:
        mention_locations = {
            (m["evidence"]["field"], m["evidence"]["start"], m["evidence"]["end"])
            for m in current["mentions"]
        }
        matched = len(locations & mention_locations)
        old_mentions_without_overlap = sum(
            not any(
                f == new_f and s < new_e and e > new_s for new_f, new_s, new_e in mention_locations
            )
            for f, s, e in old_locations
        )
    fixed_literal_comparison.append(
        {
            "document_id": document_id,
            "baseline_literal_hint_locations": len(locations),
            "after_has_accepted_candidate": current is not None,
            "exact_locations_with_after_mention": matched,
            "baseline_mention_locations": len(old_locations),
            "baseline_mentions_without_any_after_overlap": old_mentions_without_overlap,
            "interpretation": "Exact location only; actor identity and true mention not judged",
        }
    )
output.parent.mkdir(parents=True, exist_ok=True)
write_json(
    output,
    {
        "run_kind": "candidate_structure_comparison_not_semantic_evaluation",
        "input_sha256": [sha256(p.read_bytes()) for p in args.documents],
        "runs": runs,
        "fixed_baseline_literal_hints": fixed_literal_comparison,
        "gold_available": False,
        "semantic_precision_recall_f1": None,
        "interpretation": (
            "Prompt, contract and output budget changed together; "
            "different counts are not accuracy or recall gains. "
            "Literal hints depend on names emitted in each run "
            "and are not a fixed recall denominator. "
            "Candidate validation can mark an extraction failure as missing; "
            "source-run outcomes retain the actual failure reason."
        ),
    },
)
print(output)
