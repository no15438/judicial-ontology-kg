"""Prevent overlong/incomplete repair responses without weakening uncertainty checks."""

import copy
import json

import pytest
from test_anchored import PROVENANCE
from test_selector_repair import CONTRACT, example

from judicial_kg.extraction.draft import DraftError
from judicial_kg.extraction.selector_repair import ConstrainedSelectorRepair


def protocol():
    doc, draft, plan, choices = example()
    key = doc["document_id"]
    return doc, ConstrainedSelectorRepair({key: draft}, {key: plan}, [key], {}), choices


@pytest.mark.parametrize(
    "change",
    [
        lambda c: c["selector_choices"][0].update(reason="x" * 81),
        lambda c: c["selector_choices"].pop(),
        lambda c: c["selector_choices"][1].update(item_id=c["selector_choices"][0]["item_id"]),
        lambda c: c["selector_choices"][0].update(item_id="outside"),
        lambda c: c["selector_choices"][0].update(choice_id="outside"),
        lambda c: c["selector_choices"][0].update(action="uncertain"),
    ],
)
def test_backend_ignoring_grammar_still_cannot_force_acceptance(change):
    doc, repair, choices = protocol()
    change(choices)
    with pytest.raises(DraftError):
        repair.materialize(json.dumps(choices), doc, CONTRACT, PROVENANCE)


def test_generator_catalog_is_frozen_and_semantic_fields_do_not_change():
    doc, repair, _ = protocol()
    before = copy.deepcopy(repair.plans)
    bound = repair.response_format(doc, repair.draft_schema(CONTRACT))["schema"]
    array = bound["properties"]["selector_choices"]
    assert array["minItems"] == array["maxItems"] == 2
    assert array["items"]["properties"]["reason"]["maxLength"] == 80
    assert set(array["items"]["properties"]["item_id"]["enum"]) == {"s0", "s1"}
    assert repair.plans == before


def test_v3_literal_text_is_not_a_choice_id_and_v2_schema_stays_immutable():
    import copy

    from jsonschema import Draft202012Validator
    from test_selector_repair import example

    from judicial_kg.extraction.selector_repair import (
        ConstrainedSelectorRepair,
        LocalSpanSelectorRepair,
    )

    doc, draft, plan, choices = example()
    key = doc["document_id"]
    args = ({key: draft}, {key: plan}, [key], {})
    old = ConstrainedSelectorRepair(*args).bound_schema(doc)
    old_before = copy.deepcopy(old)
    new = LocalSpanSelectorRepair(*args).bound_schema(doc)
    op = old["properties"]["selector_choices"]["items"]["properties"]
    np = new["properties"]["selector_choices"]["items"]["properties"]
    assert op["local_text"]["enum"] == op["choice_id"]["enum"]  # historical V2 bug retained
    assert "enum" not in np["local_text"] and np["choice_id"]["enum"] == op["choice_id"]["enum"]
    assert old == old_before
    choices["selector_choices"][0].update(
        action="local_span", choice_id=None, local_text="A🙂", mention_kind="name"
    )
    assert list(Draft202012Validator(new).iter_errors(choices)) == []
    assert list(Draft202012Validator(old).iter_errors(choices))
