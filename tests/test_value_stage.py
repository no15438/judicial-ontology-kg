"""Exact value coordinates cannot borrow another event's source or alter content."""

import copy

import pytest
from test_anchored import source

from judicial_kg.extraction import space_inventory, value_stage
from judicial_kg.extraction.draft import DraftError
from judicial_kg.sources.records import canonical_hash


def fixture():
    doc = source()
    context = {"assertion_units": ["u0", "u1"]}
    facts = {
        "roles": [],
        "statements": [],
        "events": [
            {
                "id": "e",
                "evidence_units": ["u1"],
                "statements": [
                    {
                        "id": "s",
                        "predicate": "principal_amount",
                        "polarity": "negated",
                        "value": {"source": {"text": "42"}},
                    }
                ],
            }
        ],
    }
    catalog = space_inventory.build_catalog(doc, ["u0", "u1"], [], [{"id": "v0", "text": "42"}])
    result = {
        "catalog_sha256": canonical_hash(catalog),
        "selections": [{"proposal_id": "v0", "span_id": catalog["entries"][0]["span_id"]}],
    }
    return doc, context, facts, catalog, result


def test_value_selection_changes_only_source_selector():
    doc, context, facts, catalog, result = fixture()
    before = copy.deepcopy(facts)
    compiled = value_stage.compile_values(doc, context, facts, catalog, result)
    statement = compiled["facts"]["events"][0]["statements"][0]
    assert statement["value"]["source"] == {"unit_id": "u1", "text": "42", "occurrence": 0}
    assert statement["polarity"] == "negated"
    assert statement["predicate"] == "principal_amount"
    assert facts == before
    assert compiled["unresolved_proposals"] == []
    assert not compiled["semantic_values_verified"]


@pytest.mark.parametrize("change", ["owner", "proposal", "ledger", "hash"])
def test_value_selection_cannot_change_ownership_or_lineage(change):
    doc, context, facts, catalog, result = fixture()
    if change == "owner":
        facts["events"][0]["evidence_units"] = ["u0"]
    elif change == "proposal":
        facts["events"][0]["statements"][0]["value"]["source"]["text"] = "B"
    elif change == "ledger":
        result["selections"] = []
    else:
        result["catalog_sha256"] = "changed"
    with pytest.raises(DraftError):
        value_stage.compile_values(doc, context, facts, catalog, result)


def test_undecided_value_is_preserved_and_never_auto_selected():
    doc, context, facts, catalog, result = fixture()
    result["selections"][0]["span_id"] = None
    compiled = value_stage.compile_values(doc, context, facts, catalog, result)
    assert compiled["status"] == "unresolved"
    assert compiled["unresolved_proposals"] == ["v0"]
    assert compiled["facts"] == facts
