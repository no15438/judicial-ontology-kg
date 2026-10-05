"""Whitespace candidate expansion preserves exact evidence and explicit choice."""

import copy

import pytest

from judicial_kg.extraction import span_catalog
from judicial_kg.extraction.space_inventory import build_catalog
from judicial_kg.sources.records import canonical_hash, document


def source(text):
    return document(
        "metadata",
        {"record_pointer": "/0"},
        {"field": ("/0", text)},
        family_id="metadata:spaces",
        allocation="development_exposed",
    )


def test_space_variants_preserve_source_and_all_positions_without_selecting():
    doc = source("A B。A　B。A  B。A\tB。AB。Ａ B。")
    before = canonical_hash(doc)
    scope = ["u0", "u1", "u2", "u3", "u4", "u5"]
    c = build_catalog(doc, scope, [], [{"id": "p", "text": "A B"}])
    assert len(c["entries"]) == 3
    assert {e["selector"]["text"] for e in c["entries"]} == {"A B", "A　B", "A  B"}
    assert c["proposals"] == [{"id": "p", "text": "A B"}]
    assert canonical_hash(doc) == before
    assert len(c["proposal_ledger"][0]["span_ids"]) == 3
    span_catalog.verify_catalog(doc, scope, c)
    selection = {
        "catalog_sha256": canonical_hash(c),
        "mentions": [],
        "proposal_decisions": [{"proposal_id": "p", "action": "uncertain", "span_ids": []}],
    }
    assert span_catalog.compile_selection(doc, scope, c, ["a"], selection)["status"] == "unresolved"
    e = c["entries"][1]
    selection["mentions"] = [{"span_id": e["span_id"], "actor_id": "a", "kind": "name"}]
    selection["proposal_decisions"][0].update(action="select", span_ids=[e["span_id"]])
    result = span_catalog.compile_selection(doc, scope, c, ["a"], selection)
    assert result["mentions"][0]["evidence"]["text"] == "A　B"
    assert not result["semantic_attribution_verified"]


def test_variants_do_not_cross_units_or_expand_source_scope():
    doc = source("A。 B。A　B。")
    c = build_catalog(doc, ["u0", "u1"], [], [{"id": "p", "text": "A B"}])
    assert c["entries"] == []
    assert c["proposal_ledger"][0]["status"] == "no_source_match"
    expanded = copy.deepcopy(c)
    expanded["direct_units"].append("u2")
    with pytest.raises(ValueError):
        span_catalog.verify_catalog(doc, ["u0", "u1"], expanded)
