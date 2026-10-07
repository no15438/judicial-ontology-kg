"""Source interval protocol simulations, not judicial cases or expert gold."""

import copy
import json

import pytest
from test_current import ROOT, FakeClient, frame, source
from test_current_recovery import DirectCalls

from judicial_kg.current.runner import run_document
from judicial_kg.current.scope import plan
from judicial_kg.current.source import batches


def interval(first, last, **extras):
    return {
        "first": first,
        "last": last,
        "kind_hint": "无法确定",
        "attribution_mode": "unknown",
        "parent_cue": None,
        "reason": "protocol fixture",
        **extras,
    }


def payload():
    return {
        "target_units": [{"unit": i, "field": "metadata", "text": f"row{i}。"} for i in range(3)],
        "background_units": [{"unit": 3, "field": "metadata", "text": "outer cue。"}],
    }


def test_source_only_partition_preserves_positions_and_literal_parent_without_fact_labels():
    value = {
        "intervals": [
            interval(
                0,
                1,
                attribution_mode="reported",
                parent_cue={"unit": 3, "text": "outer cue", "occurrence": 0},
            ),
            interval(2, 2),
        ]
    }
    before = copy.deepcopy(payload())
    saved = {}
    result = plan(payload(), DirectCalls([value]), "p", lambda k, v: saved.update({k: v}))
    assert [r["units"] for r in result] == [[0, 1], [2]]
    assert all(r["not_gold"] and not r["semantic_verified"] for r in result)
    assert payload() == before
    assert result[0]["parent_cue"] == value["intervals"][0]["parent_cue"]
    assert saved["p-source-intervals"]["target_units"] == [0, 1, 2]


@pytest.mark.parametrize(
    "items",
    [
        [interval(0, 1), interval(1, 2)],  # duplicate unit
        [interval(0, 0), interval(2, 2)],  # omitted unit
        [interval(1, 2), interval(0, 0)],  # reorder
        [interval(0, 1)],  # missing tail
        [interval(0, 2, attribution_mode="quoted")],  # fabricated nesting without cue
        [interval(0, 2, parent_cue={"unit": 3, "text": "invented", "occurrence": 0})],
        [interval(0, 2, parent_cue={"unit": 3, "text": "outer", "occurrence": 1})],
    ],
)
def test_partition_errors_cannot_be_silently_accepted_or_drop_the_denominator(items):
    calls = DirectCalls([{"intervals": items}] * 3)
    saved = {}
    with pytest.raises(ValueError, match="protocol_recovery_exhausted"):
        plan(payload(), calls, "p", lambda k, v: saved.update({k: v}))
    assert len(calls.stages) == 3
    assert len(saved["p-source-partition-recovery"]) == 3


def test_partition_cannot_bridge_original_fields():
    p = payload()
    p["target_units"][1]["field"] = "other"
    with pytest.raises(ValueError, match="protocol_recovery_exhausted"):
        plan(p, DirectCalls([{"intervals": [interval(0, 2)]}] * 3), "p", lambda *a: None)


def test_disjoint_intervals_do_not_promote_model_kind_hints_or_expand_evidence(tmp_path):
    doc = source()
    doc["fields"] = doc["fields"][:1]
    empty = {
        "contexts": [],
        "coverage": [{"unit": 0, "status": "no_relevant_content", "reason": "fixture"}],
    }
    proposal = {
        "contexts": [frame()],
        "coverage": [
            {
                "unit": i,
                "status": "processed" if i == 1 else "no_relevant_content",
                "reason": "fixture",
            }
            for i in range(1, len(batches(doc)[0]))
        ],
    }
    verification = {
        f"c0_{k}_0": {"decision": "supported", "units": [1]}
        for k in ("actors", "collectives", "events")
    }
    client = FakeClient(
        [{"intervals": [interval(0, 0), interval(1, 4)]}, empty, proposal, verification]
    )
    dest = tmp_path / "run"
    result = run_document(ROOT, doc, dest, client, source_partition=True, semantic_review=False)
    assert result["source_partition_requests"] == 1
    assert result["source_intervals_proposed"] == 2
    assert result["requests"] == 4
    candidate = json.loads((dest / "candidate.json").read_text())
    assert candidate["contexts"][0]["kind"] == "quoted_material"  # did not inherit unknown hint
    coverage = json.loads((dest / "coverage.json").read_text())
    assert [r["unit"] for r in coverage] == list(range(5))
    body = json.loads((dest / "window-000-source-01-propose.request.json").read_text())
    assert json.loads(body["messages"][1]["content"])["source_interval"]["units"] == [1, 2, 3, 4]
    from judicial_kg.current.cli import implementation_fingerprint, replay
    from judicial_kg.sources.development import write_json
    from judicial_kg.sources.records import sha256

    write_json(
        dest / "config.json",
        {
            "window_size": 16,
            "implementation": implementation_fingerprint(ROOT),
            "semantic_review": False,
            "source_partition": True,
        },
    )
    write_json(
        dest / "outputs.json",
        {p.name: sha256(p.read_bytes()) for p in dest.iterdir() if p.is_file()},
    )
    replayed = replay(ROOT, dest, tmp_path / "replayed")
    assert replayed["network_calls"] == 0 and replayed["saved_requests_replayed"] == 4
    assert (dest / "source-intervals.json").read_bytes() == (
        tmp_path / "replayed/source-intervals.json"
    ).read_bytes()


def test_independent_interval_abstention_does_not_erase_neighboring_extraction(tmp_path):
    doc = source()
    doc["fields"] = doc["fields"][:1]
    abstained = {
        "contexts": [],
        "coverage": [{"unit": 0, "status": "uncertain", "reason": "fixture"}],
    }
    proposal = {
        "contexts": [frame()],
        "coverage": [
            {
                "unit": i,
                "status": "processed" if i == 1 else "no_relevant_content",
                "reason": "fixture",
            }
            for i in range(1, len(batches(doc)[0]))
        ],
    }
    verification = {
        f"c0_{k}_0": {"decision": "supported", "units": [1]}
        for k in ("actors", "collectives", "events")
    }
    dest = tmp_path / "run"
    result = run_document(
        ROOT,
        doc,
        dest,
        FakeClient(
            [{"intervals": [interval(0, 0), interval(1, 4)]}, abstained, proposal, verification]
        ),
        source_partition=True,
        semantic_review=False,
    )
    assert result["status"] == "partial" and result["counts"]["events"] == 1
    assert json.loads((dest / "coverage.json").read_text())[0]["status"] == "uncertain"
