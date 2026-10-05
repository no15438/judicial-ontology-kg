"""Same-position multi-actor mentions preserve identity and explicit uncertainty."""

from test_anchored import source

from judicial_kg.extraction.span_catalog import build_catalog, compile_selection
from judicial_kg.sources.records import canonical_hash


def test_collective_position_keeps_two_distinct_actor_mentions():
    doc = source()
    catalog = build_catalog(doc, ["u0"], [], [{"id": "p0", "text": "A🙂"}])
    span = catalog["entries"][0]["span_id"]
    result = {
        "catalog_sha256": canonical_hash(catalog),
        "mentions": [
            {"span_id": span, "actor_id": actor, "kind": "pronoun"} for actor in ["a", "b"]
        ],
        "proposal_decisions": [{"proposal_id": "p0", "action": "select", "span_ids": [span]}],
    }
    compiled = compile_selection(doc, ["u0"], catalog, ["a", "b"], result)
    assert compiled["status"] == "source_grounded_stage"
    assert len({m["id"] for m in compiled["mentions"]}) == 2
    assert {m["actor_id"] for m in compiled["mentions"]} == {"a", "b"}
    assert compiled["mentions"][0]["evidence"] == compiled["mentions"][1]["evidence"]
    assert not compiled["semantic_attribution_verified"]
    result["proposal_decisions"][0].update(action="uncertain", span_ids=[])
    assert compile_selection(doc, ["u0"], catalog, ["a", "b"], result)["status"] == "unresolved"
