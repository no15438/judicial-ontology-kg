"""Protocol recovery never invents source coverage or turns quarantine into gold."""

import copy

import pytest
from test_current import compiler, frame

from judicial_kg.current.acceptance import assess
from judicial_kg.current.recovery import infer_checked, proposal_check
from judicial_kg.current.runner import verify


class DirectCalls:
    halted = False

    def __init__(self, values):
        self.values = iter(values)
        self.stages = []

    def infer(self, messages, stage):
        self.stages.append(stage)
        return next(self.values)


def test_invalid_verification_item_does_not_erase_valid_neighbors():
    first = {
        "c0_actors_0": {"decision": "supported", "units": [0]},
        "c0_collectives_0": {"decision": "supported", "units": [1]},
        "c0_events_0": {"decision": "supported", "units": [999]},
    }
    calls = DirectCalls([first, {"c0_events_0": {"decision": "supported", "units": [999]}}])
    saved = {}
    out = verify(
        {"contexts": [frame()]},
        {
            "target_units": [{"unit": 1, "text": "x"}],
            "background_units": [{"unit": 0, "text": "a"}],
        },
        calls,
        "w",
        lambda k, v: saved.update({k: v}),
    )
    assert out["c0_actors_0"]["decision"] == "supported"
    assert out["c0_collectives_0"]["decision"] == "supported"
    assert out["c0_events_0"] == {"decision": "uncertain", "units": []}
    assert saved["w-verification-audit"] == [
        {"key": "c0_events_0", "status": "protocol_unresolved"}
    ]
    assert len(calls.stages) == 2


def test_verification_item_can_recover_without_reasking_valid_items():
    first = {
        f"c0_{k}_0": {"decision": "supported", "units": [1]}
        for k in ("actors", "collectives", "events")
    }
    first["c0_events_0"]["units"] = [999]
    calls = DirectCalls([first, {"c0_events_0": {"decision": "supported", "units": [1]}}])
    saved = {}
    out = verify(
        {"contexts": [frame()]},
        {"target_units": [{"unit": 1, "text": "x"}], "background_units": []},
        calls,
        "w",
        lambda k, v: saved.update({k: v}),
    )
    assert out["c0_events_0"]["units"] == [1]
    assert not saved["w-verification-audit"]


def test_proposal_recovery_asks_model_to_supply_missing_coverage():
    c, fmt = compiler()
    targets = list(range(len(c.units)))
    good = {
        "contexts": [frame()],
        "coverage": [
            {
                "unit": i,
                "status": "processed" if i == 1 else "no_relevant_content",
                "reason": "fixture",
            }
            for i in targets
        ],
    }
    bad = copy.deepcopy(good)
    del bad["coverage"]
    calls = DirectCalls([bad, good])
    saved = {}
    out = infer_checked(
        calls, [], "p", lambda x: proposal_check(x, fmt, targets), lambda k, v: saved.update({k: v})
    )
    assert out == good
    assert calls.stages == ["p", "p-protocol-repair-1"]
    assert "coverage" not in saved["p-parsed"]
    assert len(saved["p-recovery"]) == 1


def test_protocol_recovery_is_bounded():
    calls = DirectCalls([None, None, None])
    saved = {}

    def check(x):
        raise ValueError("wire_schema_invalid")

    with pytest.raises(ValueError, match="protocol_recovery_exhausted"):
        infer_checked(calls, [], "p", check, lambda k, v: saved.update({k: v}))
    assert len(calls.stages) == 3


def test_engineering_gate_does_not_accept_failed_units_or_format_errors():
    record = {"events": [{}], "statements": [{}]}
    coverage = [{"unit": 0, "status": "processed"}]
    unresolved = [
        {"status": "model_unresolved"},
        {"code": "verifier_rejected"},
        {"code": "dependency_quarantined"},
    ]
    assert assess(record, unresolved, coverage, [], 1)["engineering_pipeline_passed"]
    for issue in (
        {"code": "KeyError"},
        {"code": "wire_schema_invalid"},
        {"status": "window_failed"},
    ):
        assert not assess(record, unresolved + [issue], coverage, [], 1)[
            "engineering_pipeline_passed"
        ]
    assert not assess(record, [], [{"unit": 0, "status": "failed"}], [], 1)[
        "engineering_pipeline_passed"
    ]
    assert not assess(record, [], coverage, [{"status": "protocol_unresolved"}], 1)[
        "engineering_pipeline_passed"
    ]
    assert not assess({"events": [], "statements": []}, [], coverage, [], 1)[
        "engineering_pipeline_passed"
    ]
