"""Owner-specific property choices reject another event's otherwise valid source ID."""

import copy

import pytest
from test_anchored import source
from test_scoped import CONTRACT, fixtures

from judicial_kg.extraction import evidence_first, owner_first, scoped
from judicial_kg.extraction.draft import DraftError


def fixture():
    doc = source()
    header = fixtures()
    context = copy.deepcopy(header["contexts"][0])
    context["assertion_units"] = ["u0", "u1"]
    base = scoped.body_schema(header, context, CONTRACT)
    catalog = evidence_first.build_evidence_catalog(doc, context)
    facts = {
        "roles": [],
        "statements": [],
        "events": [
            {"id": "ea", "evidence_units": ["u0"], "statements": []},
            {"id": "eb", "evidence_units": ["u1"], "statements": []},
        ],
    }
    schema = owner_first.property_schema(base, facts, context, catalog)
    choices = {e["selector"]["unit_id"]: f"v{i}" for i, e in enumerate(catalog["entries"])}
    result = {
        "statements": [],
        "event_statements": {
            "ea": [],
            "eb": [
                {
                    "id": "s",
                    "predicate": "other",
                    "polarity": "negated",
                    "value": {"kind": "text", "source": {"span_id": choices["u1"]}},
                }
            ],
        },
    }
    return doc, context, catalog, facts, schema, result, choices


def test_owner_bound_properties_keep_events_immutable():
    doc, context, catalog, facts, schema, result, _ = fixture()
    before = copy.deepcopy(facts)
    compiled = owner_first.apply_properties(doc, context, catalog, facts, result, schema)
    assert compiled["events"][1]["statements"][0]["value"]["source"]["unit_id"] == "u1"
    assert compiled["events"][1]["evidence_units"] == ["u1"]
    assert facts == before


@pytest.mark.parametrize("change", ["other_owner", "missing_owner", "new_owner", "edit_events"])
def test_schema_prevents_cross_owner_properties_and_skeleton_edits(change):
    doc, context, catalog, facts, schema, result, choices = fixture()
    if change == "other_owner":
        result["event_statements"]["eb"][0]["value"]["source"]["span_id"] = choices["u0"]
    elif change == "missing_owner":
        del result["event_statements"]["ea"]
    elif change == "new_owner":
        result["event_statements"]["unknown"] = []
    else:
        result["events"] = [{"id": "ea", "evidence_units": ["u1"]}]
    with pytest.raises(DraftError):
        owner_first.apply_properties(doc, context, catalog, facts, result, schema)


def test_duplicate_event_ids_cannot_collapse_property_slots():
    doc, context, catalog, facts, _, _, _ = fixture()
    facts["events"][1]["id"] = "ea"
    with pytest.raises(DraftError, match="duplicate_event_id"):
        owner_first.property_schema(
            scoped.body_schema(fixtures(), context, CONTRACT), facts, context, catalog
        )
