"""Metadata tests for speaker-local unsupported properties, not semantic gold."""

import copy

import pytest
from test_selector_repair import CONTRACT, decision, value_example

from judicial_kg.extraction import local_review, selector_repair
from judicial_kg.extraction.anchored import GroundingError
from judicial_kg.extraction.draft import DraftError


def fixture():
    doc, draft, positions = value_example(False)
    ds = {
        "selector_choices": [
            decision(i, "uncertain" if i["target_kind"] == "statement_value" else "locate")
            for i in positions["selector_items"]
        ]
    }
    staged, _, _ = selector_repair.stage_choices(draft, positions, ds)
    plan = local_review.build_plan(doc, staged, CONTRACT)
    review = {
        "context_choices": [],
        "mention_choices": [],
        "actor_choices": [],
        "property_choices": [
            {
                "statement_id": "v",
                "action": "reject_outside_context",
                "reason": "metadata absent in speaker scope",
            }
        ],
    }
    return staged, plan, review


def test_rejection_preserves_event_amount_polarity_and_source_record():
    draft, plan, ds = fixture()
    original = copy.deepcopy(draft)
    patched, rejected = local_review.apply_choices(draft, plan, ds)
    assert draft == original
    assert (
        rejected[0]["original_assertion"] == original["contexts"][0]["events"][0]["statements"][0]
    )
    patched["contexts"][0]["events"][0]["statements"] = original["contexts"][0]["events"][0][
        "statements"
    ]
    assert patched == original


def test_uncertain_remains_rejected_but_stage_records_no_property_deletion():
    draft, plan, ds = fixture()
    ds["property_choices"][0]["action"] = "uncertain"
    staged, rejected, pending = local_review.stage_choices(draft, plan, ds)
    assert staged == draft and rejected == [] and len(pending) == 1
    with pytest.raises(GroundingError):
        local_review.apply_choices(draft, plan, ds)


@pytest.mark.parametrize(
    "change",
    [
        lambda ds: ds["property_choices"][0].update(statement_id="other"),
        lambda ds: ds["property_choices"][0].update(value={"date": "changed"}),
        lambda ds: ds["property_choices"].append(ds["property_choices"][0]),
    ],
)
def test_cannot_change_literal_or_delete_other_properties(change):
    draft, plan, ds = fixture()
    change(ds)
    with pytest.raises(DraftError):
        local_review.apply_choices(draft, plan, ds)
