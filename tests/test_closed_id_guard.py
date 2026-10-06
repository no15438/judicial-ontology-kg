"""Synthetic transport-contract tests; no case gold."""

import copy
import json

import pytest

from judicial_kg.extraction.closed_id_guard import ANONYMOUS_INSTRUCTION, ClosedIdGuard
from judicial_kg.extraction.draft import DraftError

SCHEMA = {
    "type": "object",
    "properties": {"assertion_units": {"type": "array", "items": {"enum": ["u0", "u1"]}}},
    "required": ["assertion_units"],
    "additionalProperties": False,
}


def guard(values):
    calls, saved = [], {}

    def infer(messages, fmt, stage):
        calls.append((copy.deepcopy(messages), fmt, stage))
        return json.dumps(values.pop(0))

    return ClosedIdGuard(infer, saved.__setitem__), calls, saved


def test_header_ids_are_reselected_not_coerced_and_original_is_preserved():
    g, calls, saved = guard([{"assertion_units": [0]}, {"assertion_units": ["u1"]}])
    request = [{"role": "system", "content": "original"}, {"role": "user", "content": "source"}]
    before = copy.deepcopy(request)
    output = g.call(request, {"schema": SCHEMA}, "header")
    assert json.loads(output) == {"assertion_units": ["u1"]}
    assert request == before
    assert calls[1][2] == "header-repair-closed-id-1"
    assert saved["header-closed-id-original"] == {"assertion_units": [0]}
    assert saved["header-closed-id-repair-receipt"]["remaining_schema_errors"] == 0


def test_repeated_invalid_response_stops_after_one_paid_correction():
    g, calls, _ = guard([{"assertion_units": [0]}, {"assertion_units": [0]}])
    with pytest.raises(DraftError, match="header_closed_id_unresolved"):
        g.call([{"role": "system", "content": "x"}], {"schema": SCHEMA}, "header")
    assert len(calls) == 2


@pytest.mark.parametrize("value", [{}, {"assertion_units": ["u0"], "extra": 1}])
def test_unrelated_schema_errors_do_not_trigger_this_repair(value):
    g, calls, saved = guard([value])
    assert (
        json.loads(g.call([{"role": "system", "content": "x"}], {"schema": SCHEMA}, "header"))
        == value
    )
    assert len(calls) == 1
    assert saved["header-closed-id-diagnostics"]["eligible"] is False


def test_actual_anonymous_request_is_replaced_but_other_stages_are_unchanged():
    g, calls, _ = guard([{"forms": []}, {"forms": []}])
    request = [{"role": "system", "content": "legacy"}, {"role": "user", "content": "source"}]
    g.call(request, {"schema": {}}, "anonymous-forms")
    g.call(request, {"schema": {}}, "body-000-forms")
    assert calls[0][0][0]["content"] == ANONYMOUS_INSTRUCTION
    assert calls[0][0][1] == request[1]
    assert calls[1][0] == request
    assert request[0]["content"] == "legacy"


def test_nested_choice_array_gets_one_model_correction_not_program_flattening():
    schema = {
        "type": "object",
        "properties": {
            "replacements": {"type": "array", "items": {"type": "string", "enum": ["x", "y"]}}
        },
    }
    g, calls, saved = guard([{"replacements": [["x", "y"]]}, {"replacements": ["y"]}])
    value = g.call(
        [{"role": "system", "content": "source"}], {"schema": schema}, "inventory-forms-repair-1"
    )
    assert json.loads(value) == {"replacements": ["y"]}
    assert len(calls) == 2
    assert saved["inventory-forms-repair-1-closed-id-original"] == {"replacements": [["x", "y"]]}
