import json
from pathlib import Path

from judicial_kg.extraction import span_catalog as p
from judicial_kg.sources.development import write_json
from judicial_kg.sources.records import canonical_hash, verify_evidence

root = Path(__file__).resolve().parents[1]


def read(path):
    return json.loads((root / path).read_text())


diag = read("artifacts/scoped-v12/v13-failed-selector-diagnostic.json")
header = read("artifacts/qwen-extraction/fresh-scoped-v13-spark439/stage-000-header-draft.json")
matches = [
    d
    for d in read("artifacts/scoped-v12/spark-documents.json")
    if d["document_id"]
    == "spark_qa:439645deb3d015aa2dd048ed1a4968404905361b10f5b228d9ee78ea779cc998"
]
if len(matches) != 1:
    raise ValueError("Expected exactly the registered source document")
doc = matches[0]
ctx = next(c for c in header["contexts"] if c["id"] == diag["context_id"])
c = p.build_catalog(
    doc, ctx["assertion_units"], [], [{"id": "reported_form", "text": diag["selector"]["text"]}]
)
positions = [
    {
        "unit_id": e["selector"]["unit_id"],
        "start": e["evidence"]["start"],
        "end": e["evidence"]["end"],
    }
    for e in c["entries"]
]
assert positions == diag["exact_literal_options_within_direct_scope"]
actors = [a["id"] for a in header["actors"]]
for e in c["entries"]:
    verify_evidence(doc, e["evidence"])
    selection = {
        "catalog_sha256": canonical_hash(c),
        "mentions": [
            {"span_id": e["span_id"], "actor_id": diag["actor_id"], "kind": "description"}
        ],
        "proposal_decisions": [
            {"proposal_id": "reported_form", "action": "select", "span_ids": [e["span_id"]]}
        ],
    }
    result = p.compile_selection(doc, ctx["assertion_units"], c, actors, selection)
    assert result["mentions"][0]["evidence"] == e["selector"]
    assert not result["document_accepted"]
empty = {
    "catalog_sha256": canonical_hash(c),
    "mentions": [],
    "proposal_decisions": [{"proposal_id": "reported_form", "action": "uncertain", "span_ids": []}],
}
assert p.compile_selection(doc, ctx["assertion_units"], c, actors, empty)["status"] == "unresolved"
write_json(root / "artifacts/span-catalog-v1/catalog.json", c)
write_json(root / "artifacts/span-catalog-v1/selection-schema.json", p.selection_schema(c, actors))
report = {
    "version": p.VERSION,
    "validation": "offline_source_coordinate_regression_only",
    "source_document_id": doc["document_id"],
    "catalog_sha256": canonical_hash(c),
    "source_sha256": canonical_hash(doc),
    "scope_units": len(ctx["assertion_units"]),
    "exact_positions": len(positions),
    "matches_prior_diagnostic": True,
    "each_position_compiles_and_replays": True,
    "ambiguous_proposal_can_remain_unresolved": True,
    "automatic_position_selected": False,
    "model_calls": 0,
    "candidate_modified": False,
    "semantic_attribution_verified": False,
    "full_document_extraction_tested": False,
    "old_v13_rejection_unchanged": True,
}
write_json(root / "research/span-catalog-regression-report.json", report)
print(json.dumps(report, indent=2))
