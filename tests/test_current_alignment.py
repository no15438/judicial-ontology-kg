"""Closed-choice alignment properties on synthetic fixtures, not legal labels."""

import copy

import pytest
from test_current import compiler, frame

from judicial_kg.current.alignment import apply, tasks_for
from judicial_kg.current.decision import BoundedDecisionClient, choices, request
from judicial_kg.extraction.transport import Response


def test_position_alignment_preserves_source_and_original_proposal():
    c, _ = compiler()
    f = frame()
    f["actors"][0]["mention"]["unit"] = 0
    wire = {"contexts": [f]}
    original = copy.deepcopy(wire)
    tasks, diagnostics = tasks_for(wire, c.doc, c.units)
    task = next(t for t in tasks if t["path"][-1] == "mention")
    decisions = {t["id"]: "unknown" for t in tasks}
    decisions[task["id"]] = "o0"
    adapted, ledger, _ = apply(wire, tasks, decisions)
    assert adapted["contexts"][0]["actors"][0]["mention"]["unit"] == 1
    assert wire == original
    assert ledger[0]["status"] == "changed"
    assert not diagnostics


def test_unknown_binding_is_blocked_not_forced():
    c, _ = compiler()
    f = frame()
    f["roles"] = [{"actor": 12, "role": "other", "polarity": "affirmed", "units": [1]}]
    wire = {"contexts": [f]}
    tasks, _ = tasks_for(wire, c.doc, c.units)
    adapted, _, blocked = apply(wire, tasks, {t["id"]: "unknown" for t in tasks})
    assert adapted == wire
    assert ["contexts", 0, "roles", 0] in blocked
    with pytest.raises(ValueError, match="outside_inventory"):
        apply(wire, tasks, {t["id"]: "o999" for t in tasks})


def test_no_candidate_not_invented_and_invalid_selector_recorded():
    c, _ = compiler()
    f = frame()
    f["actors"][0]["mention"]["text"] = "NONEXISTENT"
    f["objects"] = [{"object_type": "other"}]
    _, diagnostics = tasks_for({"contexts": [f]}, c.doc, c.units)
    assert {d["status"] for d in diagnostics} == {"no_bounded_candidates", "invalid_selector"}


def test_native_protocol_rejects_extra_choice_and_unmatched_ids():
    task = {"id": "a0", "state": {}, "instruction": "选择", "options": {"o0": "候选"}}
    body = request([task])
    answer = {
        "model": "decision-model-preview",
        "answers": {
            "a0": {"type": "choice", "choice": "o0", "probabilities": {"o0": 0.8, "unknown": 0.2}}
        },
    }
    assert choices(body, answer) == {"a0": "o0"}
    answer["answers"]["a0"]["choice"] = "o9"
    with pytest.raises(ValueError):
        choices(body, answer)
    with pytest.raises(ValueError):
        request([task] * 17)


def test_decision_circuit_prevents_repeated_auth_calls():
    class Client:
        calls = 0

        def call(self, body, timeout):
            self.calls += 1
            return Response(403, b"{}", 0)

    client = Client()
    bounded = BoundedDecisionClient(client)
    assert bounded.call({}, 1).status == 403
    assert bounded.call({}, 1).error == "decision_request_limit"
    assert client.calls == 1


def test_aligned_pipeline_replays_native_calls_without_network(tmp_path):
    import json

    from test_current import ROOT, FakeClient, source

    from judicial_kg.current.cli import implementation_fingerprint, replay
    from judicial_kg.current.runner import run_document
    from judicial_kg.current.source import batches
    from judicial_kg.sources.development import write_json
    from judicial_kg.sources.records import sha256

    doc = source()
    doc["fields"] = doc["fields"][:1]
    units, _ = batches(doc)
    f = frame()
    f["actors"][0]["mention"]["unit"] = 0
    proposal = {
        "contexts": [f],
        "coverage": [
            {
                "unit": i,
                "status": "processed" if i == 1 else "no_relevant_content",
                "reason": "fixture",
            }
            for i in range(len(units))
        ],
    }
    verification = {
        f"c0_{k}_0": {"decision": "supported", "units": [1]}
        for k in ("actors", "collectives", "events")
    }

    class Native:
        def call(self, body, timeout):
            answers = {
                k: {
                    "type": "choice",
                    "choice": "o0",
                    "probabilities": {o: float(o == "o0") for o in q["criteria"]},
                }
                for k, q in body["questions"].items()
            }
            return Response(
                200, json.dumps({"model": "decision-model-preview", "answers": answers}).encode(), 0
            )

    dest = tmp_path / "run"
    result = run_document(
        ROOT, doc, dest, FakeClient([proposal, verification]), alignment_client=Native()
    )
    assert result["alignment_requests"] == 1
    assert result["status"] == "engineering_complete"
    write_json(
        dest / "config.json",
        {
            "window_size": 16,
            "implementation": implementation_fingerprint(ROOT),
            "alignment": "decision",
        },
    )
    write_json(dest / "outputs.json", {p.name: sha256(p.read_bytes()) for p in dest.iterdir()})
    assert replay(ROOT, dest, tmp_path / "replay")["offline_replay_verified"]
