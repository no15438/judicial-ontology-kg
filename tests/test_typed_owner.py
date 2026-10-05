"""Predicate/literal compatibility complements, rather than replaces, source ownership."""

import copy

import pytest
from test_owner_first import fixture
from test_scoped import CONTRACT, fixtures

from judicial_kg.extraction import scoped, typed_owner
from judicial_kg.extraction.context_first import check_schema
from judicial_kg.extraction.draft import DraftError


@pytest.mark.parametrize(
    "literal,kinds",
    [
        ("32万元", {"money", "text"}),
        ("2013年5月30日", {"date", "text"}),
        ("4倍", {"rate", "text"}),
        ("A🙂 B 42。", {"text"}),
        ("42", {"text"}),
    ],
)
def test_literal_type_is_conservative_not_semantic_inference(literal, kinds):
    assert typed_owner.literal_kinds(literal) == kinds


def test_arbitrary_text_cannot_be_filled_as_date_or_money():
    _, context, catalog, facts, _, result, _ = fixture()
    schema = typed_owner.property_schema(
        scoped.body_schema(fixtures(), context, CONTRACT), facts, context, catalog
    )
    check_schema(result, schema)
    for predicate in ["event_date", "principal_amount"]:
        bad = copy.deepcopy(result)
        statement = bad["event_statements"]["eb"][0]
        statement["predicate"] = predicate
        statement["value"].pop("kind")
        with pytest.raises(DraftError):
            check_schema(bad, schema)


def test_schema_still_rejects_other_event_literal():
    _, context, catalog, facts, _, result, choices = fixture()
    schema = typed_owner.property_schema(
        scoped.body_schema(fixtures(), context, CONTRACT), facts, context, catalog
    )
    result["event_statements"]["eb"][0]["value"]["source"]["span_id"] = choices["u0"]
    with pytest.raises(DraftError):
        check_schema(result, schema)
