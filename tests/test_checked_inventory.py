"""All-discovery checks and batch ID compilation on metadata-only fixtures."""

import copy
import json

import pytest
from test_anchored import source

from judicial_kg.extraction import (
    actor_inventory,
    checked_forms,
    checked_inventory,
    fragment_repairs,
)
from judicial_kg.extraction.draft import DraftError


def property_value():
    return {
        "statements": [],
        "event_statements": {
            "e1": [
                {
                    "id": "s1",
                    "predicate": "other",
                    "polarity": "negated",
                    "value": {"kind": "text", "source": {"span_id": "v0"}},
                }
            ]
        },
    }


def test_independent_property_batches_have_unique_reversible_ids():
    original = property_value()
    snapshot = copy.deepcopy(original)
    first, receipt = checked_inventory.namespace_properties(
        original, "body-000-properties-owner-001"
    )
    second, _ = checked_inventory.namespace_properties(original, "body-000-properties-owner-002")
    assert first["event_statements"]["e1"][0]["id"] != second["event_statements"]["e1"][0]["id"]
    for alias in receipt["aliases"]:
        first["event_statements"][alias["owner"]][alias["source_index"]]["id"] = alias[
            "original_id"
        ]
    assert first == original == snapshot


def test_duplicate_ids_within_one_model_batch_remain_a_failure():
    value = property_value()
    value["event_statements"]["e1"].append(copy.deepcopy(value["event_statements"]["e1"][0]))
    with pytest.raises(DraftError, match="duplicate_property_id_within_batch"):
        checked_inventory.namespace_properties(value, "body-000-properties-owner-001")


@pytest.mark.parametrize("stage", ["inventory-forms", "anonymous-forms", "body-000-forms"])
def test_all_discovery_uses_same_bounded_source_correction(stage):
    doc = source()
    schema = actor_inventory.obj(
        {"forms": {"type": "array", "uniqueItems": True, "items": {"type": "string"}}}
    )
    saved, calls, budget = {}, [], {"used": 2}

    def infer(messages, fmt, key):
        calls.append(key)
        if key.endswith("-repair-1"):
            return json.dumps({"replacements": [{"proposal_id": "p0", "forms": ["A🙂"]}]})
        return json.dumps({"forms": ["A🙂 MISSING"]})

    checked = checked_forms.source_checked_infer(
        doc,
        ["u0"],
        infer,
        saved.__setitem__,
        budget=budget,
        repair_options=fragment_repairs.source_fragments,
    )
    messages = [
        {"role": "system", "content": "metadata"},
        {"role": "user", "content": '{"source_units": []}'},
    ]
    assert json.loads(checked(messages, {"schema": schema}, stage)) == {"forms": ["A🙂"]}
    assert budget["used"] == 3
    assert calls == [stage, stage + "-repair-1"]
    assert saved[stage + "-correction-receipt"]["counts_as_first_pass"] is False
    with pytest.raises(DraftError, match="form_correction_budget_exhausted"):
        checked(messages, {"schema": schema}, stage)
    assert calls.count(stage + "-repair-1") == 1
