"""Metadata token occurrences; classifications are simulations, not semantic gold."""

import json

import pytest
from test_anchored import source
from test_scoped import record

from judicial_kg.extraction import actor_inventory, context_first
from judicial_kg.extraction.draft import DraftError
from judicial_kg.extraction.occurrence_routing import MAX_OCCURRENCE_REVIEWS, OccurrenceRouter


def router(answers):
    calls = []
    saved = {}

    def infer(messages, fmt, stage):
        calls.append((messages, fmt, stage))
        return json.dumps(answers.pop(0))

    return OccurrenceRouter(source(), infer, saved.__setitem__), calls, saved


def test_mixed_form_routes_each_occurrence_and_retains_fragment_ledger():
    r, calls, saved = router(
        [{"p0": "uncertain"}, {"o0": "actor_candidate", "o1": "literal_fragment"}]
    )
    assert r.route({"forms": ["A"]}, ["u0", "u3"], "body-000-forms") == {"forms": ["A"]}
    assert len(calls) == 2
    assert "repair-object-occurrences" in calls[1][2]
    assert [x["kind"] for x in r.decisions] == ["actor_candidate", "literal_fragment"]
    assert len(saved["body-000-forms-repair-object-occurrences-001-ledger"]["decisions"]) == 2
    compiled = r.compile(record())
    assert compiled["objects"] == compiled["object_mentions"] == []
    assert compiled["mentions"] == record()["mentions"]


def test_mixed_object_and_actor_are_both_retained_at_distinct_positions():
    r, _, _ = router([{"p0": "uncertain"}, {"o0": "project", "o1": "actor_candidate"}])
    assert r.route({"forms": ["A"]}, ["u0", "u3"], "body-000-forms") == {"forms": ["A"]}
    compiled = r.compile(record())
    assert len(compiled["objects"]) == 1
    assert compiled["object_mentions"][0]["evidence"]["start"] == 0


@pytest.mark.parametrize(
    "answer",
    [{"o0": "uncertain", "o1": "actor_candidate"}, {"o0": "invalid", "o1": "actor_candidate"}, {}],
)
def test_unresolved_or_invalid_occurrence_response_never_drops_failure(answer):
    r, _, _ = router([{"p0": "uncertain"}, answer])
    with pytest.raises(DraftError):
        r.route({"forms": ["A"]}, ["u0", "u3"], "body-000-forms")


def test_all_fragments_have_explicit_exclusions_without_invented_entities():
    r, _, saved = router(
        [{"p0": "uncertain"}, {"o0": "literal_fragment", "o1": "literal_fragment"}]
    )
    assert r.route({"forms": ["A"]}, ["u0", "u3"], "body-000-forms") == {"forms": []}
    assert len(saved["body-000-forms-resolved-object-routing-ledger"]["decisions"]) == 2
    assert r.compile(record())["objects"] == []


def test_same_position_route_change_fails_instead_of_reinterpreting_prior_identity():
    r, _, _ = router([{"p0": "project"}, {"p0": "platform"}])
    r.route({"forms": ["42"]}, ["u1"], "body-000-forms")
    with pytest.raises(DraftError, match="object_occurrence_route_conflict"):
        r.route({"forms": ["42"]}, ["u1"], "body-001-forms")


def test_non_actor_positions_cannot_reenter_binding_schema():
    r, _, saved = router([{"p0": "uncertain"}, {"o0": "actor_candidate", "o1": "literal_fragment"}])
    r.route({"forms": ["A"]}, ["u0", "u3"], "body-000-forms")
    schema = actor_inventory.obj(
        {
            "mentions": {
                "type": "object",
                "properties": {
                    "s0": actor_inventory.obj({"a": {"const": "name"}}),
                    "s1": actor_inventory.obj({"a": {"const": "name"}}),
                },
                "additionalProperties": False,
            }
        }
    )
    payload = {
        "catalog": {
            "entries": [
                {"span_id": "s0", "unit_id": "u0", "text": "A", "occurrence": 0},
                {"span_id": "s1", "unit_id": "u3", "text": "A", "occurrence": 0},
            ]
        }
    }
    messages = [
        {"role": "system", "content": "Schema: " + json.dumps(schema, ensure_ascii=False)},
        {"role": "user", "content": json.dumps(payload)},
    ]
    request, fmt = r.restrict_mentions(messages, {"schema": schema}, "body-000-mentions")
    assert set(fmt["schema"]["properties"]["mentions"]["properties"]) == {"s0"}
    assert json.loads(request[-1]["content"])["catalog"] == payload["catalog"]
    assert saved["body-000-mentions-position-route-exclusions"] == {"s1": "literal_fragment"}
    with pytest.raises(DraftError):
        context_first.check_schema({"mentions": {"s1": {"a": "name"}}}, fmt["schema"])
    assert "s1" in schema["properties"]["mentions"]["properties"]


def test_occurrence_review_budget_counts_calls_across_stages():
    answers = []
    for _ in range(MAX_OCCURRENCE_REVIEWS):
        answers.extend([{"p0": "uncertain"}, {"o0": "actor_candidate", "o1": "literal_fragment"}])
    answers.append({"p0": "uncertain"})
    r, calls, _ = router(answers)
    for i in range(MAX_OCCURRENCE_REVIEWS):
        r.route({"forms": ["A"]}, ["u0", "u3"], f"body-{i:03}-forms")
    with pytest.raises(DraftError, match="object_occurrence_review_budget_exhausted"):
        r.route({"forms": ["A"]}, ["u0", "u3"], "body-999-forms")
    assert sum("repair-object-occurrences" in x[2] for x in calls) == MAX_OCCURRENCE_REVIEWS
