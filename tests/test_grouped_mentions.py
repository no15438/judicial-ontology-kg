"""Binding maps preserve collectives, fixed anchor kinds and unique JSON keys."""

import copy

import pytest
from test_anchored import source

from judicial_kg.extraction import context_first, grouped_mentions, span_catalog
from judicial_kg.extraction.draft import DraftError
from judicial_kg.sources.records import canonical_hash
from judicial_kg.validation_run import strict_json


def fixture():
    doc = source()
    catalog = span_catalog.build_catalog(doc, ["u0"], [], [{"id": "p0", "text": "A🙂"}])
    entry = catalog["entries"][0]
    actors = [
        {"id": "a", "mentions": [{"kind": "name", "evidence": entry["selector"]}]},
        {"id": "b", "mentions": []},
    ]
    view = {"entries": [{"span_id": entry["span_id"], **entry["selector"]}]}
    schema = grouped_mentions.grouped_schema(
        span_catalog.selection_schema(catalog, ["a", "b"]), view, actors
    )
    result = {
        "catalog_sha256": canonical_hash(catalog),
        "mentions": {entry["span_id"]: {"a": "name", "b": "pronoun"}},
        "proposal_decisions": [
            {"proposal_id": "p0", "action": "select", "span_ids": [entry["span_id"]]}
        ],
    }
    return doc, catalog, schema, result


def test_grouped_collective_compiles_two_actors_at_one_original_position():
    doc, catalog, schema, result = fixture()
    context_first.check_schema(result, schema)
    flat = grouped_mentions.expand_bindings(result)
    compiled = span_catalog.compile_selection(doc, ["u0"], catalog, ["a", "b"], flat)
    assert len(compiled["mentions"]) == 2
    assert compiled["status"] == "source_grounded_stage"


@pytest.mark.parametrize("change", ["anchor_kind", "new_actor", "new_position"])
def test_binding_map_rejects_changed_frozen_kind_and_unknown_ids(change):
    _, _, schema, result = fixture()
    result = copy.deepcopy(result)
    position = next(iter(result["mentions"]))
    if change == "anchor_kind":
        result["mentions"][position]["a"] = "description"
    elif change == "new_actor":
        result["mentions"][position]["unknown"] = "name"
    else:
        result["mentions"]["unknown"] = {"a": "name"}
    with pytest.raises(DraftError):
        context_first.check_schema(result, schema)


def test_duplicate_json_binding_cannot_silently_replace_first_kind():
    with pytest.raises(ValueError):
        strict_json('{"mentions":{"s0":{"a":"name","a":"description"}}}')
