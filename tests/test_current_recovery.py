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
    record = {"events": [{}], "statements": [{}], "roles": [{}]}
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


def test_failed_parent_splits_into_exact_source_partition(tmp_path):
    import json

    from test_current import ROOT, FakeClient, source

    from judicial_kg.current.runner import run_document
    from judicial_kg.current.source import batches

    doc = source()
    doc["fields"] = doc["fields"][:1]
    units, windows = batches(doc)
    target = windows[0]
    middle = len(target) // 2

    def empty(ids):
        return {
            "contexts": [],
            "coverage": [
                {"unit": i, "status": "no_relevant_content", "reason": "fixture"} for i in ids
            ],
        }

    client = FakeClient([{}] * 3 + [empty(target[:middle]), empty(target[middle:])])
    report = run_document(ROOT, doc, tmp_path / "run", client, max_split_depth=1)
    coverage = json.loads((tmp_path / "run/coverage.json").read_text())
    assert [r["unit"] for r in coverage] == list(range(len(units)))
    assert report["split_attempts"] == 1 and report["failed_leaf_windows"] == 0
    assert not report["engineering_pipeline_passed"]  # Empty outputs cannot satisfy acceptance.
    attempts = json.loads((tmp_path / "run/window-attempts.json").read_text())
    assert attempts[0]["units"] == target and attempts[0]["recovery"] == "split"


def test_schema_repair_includes_machine_locatable_error_details():
    from judicial_kg.current.recovery import ProtocolError

    c, fmt = compiler()
    with pytest.raises(ProtocolError) as caught:
        proposal_check({"contexts": []}, fmt, list(range(len(c.units))))
    assert caught.value.details[0]["rule"] == "required"


def test_contract_feedback_recompiles_transactionally_without_manual_graph_edit():
    from judicial_kg.current.runner import compile_with_recovery

    c, fmt = compiler()
    targets = list(range(len(c.units)))
    original = frame()
    original.update(kind="court_finding", speakers=[0])  # A person is not the speaking court.
    wire = {
        "contexts": [original],
        "coverage": [{"unit": i, "status": "processed", "reason": "fixture"} for i in targets],
    }
    repaired = copy.deepcopy(wire)
    repaired["contexts"][0]["speakers"] = []
    decisions = {
        f"c0_{k}_0": {"decision": "supported", "units": [1]}
        for k in ("actors", "collectives", "events")
    }
    calls = DirectCalls([decisions, repaired, decisions])
    saved = {}
    final, name = compile_with_recovery(
        wire,
        c,
        fmt,
        targets,
        {
            "target_units": [{"unit": i, "text": u["text"]} for i, u in enumerate(c.units)],
            "background_units": [],
        },
        calls,
        None,
        c.doc,
        c.units,
        [],
        "window-000",
        lambda k, v: saved.update({k: v}),
    )
    assert name == "window-000-contract-repair-1"
    assert final["contexts"][0]["speakers"] == []
    assert len(c.record["actors"]) == 1 and len(c.record["contexts"]) == 1
    assert not c.issues
    history = saved["window-000-contract-recovery"]
    assert history[0]["issues"][0]["issues"][0]["code"] == "court_attribution_type"
    assert wire["contexts"][0]["speakers"] == [0]  # Preserve failed proposal.


@pytest.mark.parametrize("actor_type", ["unknown", "court", "organization"])
def test_explicit_panel_cannot_bypass_collective_contract_as_actor(actor_type):
    c, fmt = compiler()
    f = frame()
    f["actors"][0].update(actor_type=actor_type)
    f["actors"][0]["mention"]["text"] = "合议庭"
    c.context(f, fmt["properties"]["contexts"]["items"], {1}, "w/c0")
    assert not c.record["actors"]
    assert any(r.get("code") == "explicit_collective_as_actor" for r in c.issues)


def test_wrong_selector_reaches_alignment_but_scope_and_structure_remain_strict():
    from judicial_kg.current.recovery import ProtocolError

    c, fmt = compiler()
    targets = list(range(len(c.units)))
    f = frame()
    f["collectives"][0]["mention"]["unit"] = 999
    wire = {
        "contexts": [f],
        "coverage": [{"unit": i, "status": "processed", "reason": "fixture"} for i in targets],
    }
    with pytest.raises(ProtocolError):
        proposal_check(wire, fmt, targets)
    proposal_check(wire, fmt, targets, defer_selectors=True)
    c.context(f, fmt["properties"]["contexts"]["items"], set(targets), "w")
    assert not c.record["collectives"]  # Final compiler still rejects unaligned positions.
    f["units"] = [999]
    with pytest.raises(ProtocolError):
        proposal_check(wire, fmt, targets, defer_selectors=True)
    f["units"] = [1]
    f["collectives"][0] = {}
    with pytest.raises(ProtocolError):
        proposal_check(wire, fmt, targets, defer_selectors=True)


def test_party_speaker_repair_preserves_claim_without_promoting_to_court_fact():
    from judicial_kg.current.runner import compile_with_recovery

    c, fmt = compiler()
    targets = list(range(len(c.units)))
    f = frame()
    f.update(kind="party_defense", speakers=[])
    wire = {
        "contexts": [f],
        "coverage": [{"unit": i, "status": "processed", "reason": "fixture"} for i in targets],
    }
    repaired = copy.deepcopy(wire)
    repaired["contexts"][0]["speakers"] = [0]
    verdict = {
        f"c0_{k}_0": {"decision": "supported", "units": [1]}
        for k in ("actors", "collectives", "events")
    }
    calls = DirectCalls([verdict, repaired, verdict])
    saved = {}
    result, _ = compile_with_recovery(
        wire,
        c,
        fmt,
        targets,
        {
            "target_units": [{"unit": i, "text": u["text"]} for i, u in enumerate(c.units)],
            "background_units": [],
        },
        calls,
        None,
        c.doc,
        c.units,
        [],
        "window-000",
        lambda k, v: saved.update({k: v}),
    )
    assert result["contexts"][0]["kind"] == "party_defense"
    assert c.record["contexts"][0]["kind"] == "party_defense"
    assert c.record["contexts"][0]["attributed_actor_ids"] == [c.record["actors"][0]["id"]]
    assert not c.issues
    assert (
        saved["window-000-contract-recovery"][0]["issues"][0]["issues"][0]["code"]
        == "party_attribution_required"
    )
