"""Source coverage and attribution hints, never a semantic judge or gold labels."""

import re
from collections import Counter, defaultdict

from judicial_kg.contracts import collections

DIAGNOSTIC_VERSION = "candidate-diagnostics-v1"


def union_ranges(ranges: list[tuple[int, int]]) -> list[list[int]]:
    result = []
    for start, end in sorted(ranges):
        if result and start <= result[-1][1]:
            result[-1][1] = max(result[-1][1], end)
        else:
            result.append([start, end])
    return result


def gaps(ranges: list[list[int]], length: int) -> list[list[int]]:
    cursor, result = 0, []
    for start, end in ranges:
        if start > cursor:
            result.append([cursor, start])
        cursor = end
    if cursor < length:
        result.append([cursor, length])
    return result


def diagnose_record(record: dict, doc: dict) -> dict:
    """Requires a source-validated record. Read-only; hints never alter acceptance."""
    spans = defaultdict(lambda: defaultdict(list))
    hints = []
    for key in collections(record):
        if key in {"actors", "objects", "collectives"}:
            continue
        group = (
            "mention"
            if key in {"mentions", "object_mentions", "collective_mentions"}
            else "context"
            if key == "contexts"
            else "assertion"
        )
        for node in record[key]:
            evidence = (
                [node["evidence"]]
                if key in {"mentions", "object_mentions", "collective_mentions"}
                else node["evidence"]
            )
            for span in evidence:
                spans[span["field"]][group].append((span["start"], span["end"]))
    fields = []
    for field in doc["fields"]:
        merged = {
            group: union_ranges(spans[field["name"]][group])
            for group in ("mention", "context", "assertion")
        }
        fields.append(
            {
                "field": field["name"],
                "source_characters": field["characters"],
                "reported_processed": field["name"] in record["coverage"]["processed_fields"],
                **{
                    f"{group}_evidence_characters": sum(end - start for start, end in ranges)
                    for group, ranges in merged.items()
                },
                "outside_context_evidence_ranges": gaps(merged["context"], field["characters"]),
            }
        )

    # Search only literal, already-observed name strings. Counts are NOT a count
    # of true mentions, entities, omissions or a semantic recall denominator.
    by_quote = defaultdict(list)
    observed = set()
    for mention in record["mentions"]:
        span = mention["evidence"]
        observed.add((span["field"], span["start"], span["end"]))
        if mention["kind"] == "name" and len(span["quote"]) >= 2:
            by_quote[span["quote"]].append(mention)
    literal_ranges = set()
    for quote, mentions in by_quote.items():
        actor_ids = sorted({m["actor_id"] for m in mentions})
        if len(actor_ids) > 1:
            hints.append({"code": "same_literal_multiple_actors", "actor_ids": actor_ids})
        for field in doc["fields"]:
            for match in re.finditer("(?=" + re.escape(quote) + ")", field["text"]):
                location = (field["name"], match.start(), match.start() + len(quote))
                if location not in observed:
                    literal_ranges.add(location)

    for context in record["contexts"]:
        # Inspect each continuous interval separately; never bridge a source gap.
        intervals = defaultdict(list)
        for span in context["evidence"]:
            intervals[span["field"]].append((span["start"], span["end"]))
        snippets = [
            field["text"][start:end]
            for field in doc["fields"]
            for start, end in union_ranges(intervals[field["name"]])
        ]
        if context["kind"] in ("party_claim", "party_defense") and any(
            re.search(r"共同(?:辩称|诉称|主张|提出|陈述)", text) for text in snippets
        ):
            hints.append({"code": "joint_attribution_needs_review", "context_id": context["id"]})
        if context["kind"] == "court_finding" and any(
            re.search(r"(?:辩称|诉称|述称|证言|证实|出具.*?证明)", text) for text in snippets
        ):
            hints.append(
                {"code": "reported_material_in_finding_context", "context_id": context["id"]}
            )

    return {
        "document_id": record["document_id"],
        "diagnostic_version": DIAGNOSTIC_VERSION,
        "fields": fields,
        "literal_name_occurrences_without_mention": [
            {"field": field, "start": start, "end": end}
            for field, start, end in sorted(literal_ranges)
        ],
        "hints": hints,
        "summary": {
            "source_characters": sum(f["characters"] for f in doc["fields"]),
            **{
                f"{group}_evidence_characters": sum(
                    f[f"{group}_evidence_characters"] for f in fields
                )
                for group in ("mention", "context", "assertion")
            },
            "literal_name_occurrences_without_mention": len(literal_ranges),
            "hint_counts": dict(sorted(Counter(h["code"] for h in hints).items())),
            "context_kind_counts": dict(
                sorted(Counter(c["kind"] for c in record["contexts"]).items())
            ),
        },
        "interpretation": "engineering_coverage_and_heuristic_hints_not_semantic_recall",
        "candidate_modified": False,
        "independent_semantic_review": False,
    }
