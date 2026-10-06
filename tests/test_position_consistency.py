"""Metadata-only contradictory binding decisions; no legal correctness labels."""

import copy
import json

import pytest
from test_anchored import source

from judicial_kg.extraction import actor_inventory, object_routing
from judicial_kg.extraction.draft import DraftError
from judicial_kg.extraction.position_consistency import MAX_REVIEWS, ConsistencyReviewer


def case():
    catalog = {
        "entries": [
            {"span_id": "s0", "unit_id": "u0", "text": "A🙂", "occurrence": 0},
            {"span_id": "s1", "unit_id": "u3", "text": "A🙂", "occurrence": 0},
        ],
        "proposal_ledger": [
            {"proposal_id": "p0", "span_ids": ["s0", "s1"], "status": "requires_selection"}
        ],
    }
    schema = actor_inventory.obj(
        {
            "mentions": actor_inventory.obj(
                {
                    "s0": actor_inventory.obj({"a": {"const": "description"}}),
                    "s1": actor_inventory.obj({"b": {"const": "description"}}),
                }
            ),
            "proposal_decisions": actor_inventory.obj({"p0": {"enum": ["select", "uncertain"]}}),
        }
    )
    messages = [
        {"role": "system", "content": "metadata"},
        {
            "role": "user",
            "content": json.dumps(
                {
                    "source_units": [{"id": "u0", "text": "A🙂，"}, {"id": "u3", "text": "A🙂。"}],
                    "actor_catalog": [{"id": "a"}, {"id": "b"}],
                    "catalog": catalog,
                }
            ),
        },
    ]
    raw = {
        "mentions": {"s0": {"a": "description"}, "s1": {"b": "description"}},
        "proposal_decisions": {"p0": "uncertain"},
    }
    return messages, object_routing.response_format(source(), schema), raw


@pytest.mark.parametrize("decision", ["confirm_existing_bindings", "uncertain"])
def test_review_keeps_bindings_and_requires_explicit_model_confirmation(decision):
    messages, fmt, raw = case()
    original = copy.deepcopy(raw)
    saved = {}
    calls = []

    def infer(msg, form, stage):
        calls.append((msg, form, stage))
        return json.dumps({"p0": decision} if "repair-consistency" in stage else raw)

    reviewer = ConsistencyReviewer(source(), infer, saved.__setitem__)
    result = json.loads(reviewer.infer_stage(messages, fmt, "body-000-mentions"))
    assert result["mentions"] == raw["mentions"] == original["mentions"]
    assert raw == original
    assert result["proposal_decisions"]["p0"] == (
        "select" if decision == "confirm_existing_bindings" else "uncertain"
    )
    assert len(calls) == 2
    assert "不同位置可以指不同主体" in calls[0][0][0]["content"]
    assert calls[1][2] == "body-000-mentions-repair-consistency-001"
    payload = json.loads(calls[1][0][1]["content"])
    assert len(payload["source_positions"]) == 2
    assert payload["existing_bindings"] == raw["mentions"]
    assert saved["body-000-mentions-position-binding-original"] == original
    assert not saved["body-000-mentions-position-binding-review-receipt"]["semantic_verified"]


@pytest.mark.parametrize("decision", ["select", "uncertain"])
def test_no_contradiction_does_not_invoke_review(decision):
    messages, fmt, raw = case()
    raw["proposal_decisions"]["p0"] = decision
    if decision == "uncertain":
        raw["mentions"] = {}
        fmt["schema"]["properties"]["mentions"] = actor_inventory.obj({})
    calls = []

    def infer(*args):
        calls.append(args)
        return json.dumps(raw)

    reviewer = ConsistencyReviewer(source(), infer, lambda *_: None)
    assert json.loads(reviewer.infer_stage(messages, fmt, "body-000-mentions")) == raw
    assert len(calls) == 1


@pytest.mark.parametrize(
    "invalid",
    [{}, {"p0": "select"}, {"p0": "confirm_existing_bindings", "p1": "confirm_existing_bindings"}],
)
def test_bad_review_is_rejected_without_salvage(invalid):
    messages, fmt, raw = case()

    def infer(msg, form, stage):
        return json.dumps(invalid if "repair-consistency" in stage else raw)

    reviewer = ConsistencyReviewer(source(), infer, lambda *_: None)
    with pytest.raises(DraftError):
        reviewer.infer_stage(messages, fmt, "body-000-mentions")


def test_per_document_review_budget_stops_before_extra_call():
    messages, fmt, raw = case()
    calls = []

    def infer(msg, form, stage):
        calls.append(stage)
        return json.dumps({"p0": "uncertain"} if "repair-consistency" in stage else raw)

    reviewer = ConsistencyReviewer(source(), infer, lambda *_: None)
    for i in range(MAX_REVIEWS):
        assert json.loads(reviewer.infer_stage(messages, fmt, f"body-{i:03}-mentions")) == raw
    with pytest.raises(DraftError, match="mention_consistency_budget_exhausted"):
        reviewer.infer_stage(messages, fmt, "body-999-mentions")
    assert sum("repair-consistency" in s for s in calls) == MAX_REVIEWS
