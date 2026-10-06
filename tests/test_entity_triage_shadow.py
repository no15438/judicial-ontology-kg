"""Metadata-only entity triage, source isolation and audit replay checks."""

import copy
import json

import pytest
from test_anchored import source
from test_jev_shadow import MODEL, VERSION, response

from judicial_kg.extraction import entity_triage_shadow as p
from judicial_kg.sources.development import write_json


def answers():
    return {k: "uncertain" for k in p.QUESTIONS}


def jev_response():
    return {
        "model": p.helper_model(VERSION),
        "answers": {
            k: {
                "type": "choice",
                "choice": "uncertain",
                "probabilities": {v: float(v == "uncertain") for v in q["criteria"]},
            }
            for k, q in p.QUESTIONS.items()
        },
    }


def test_source_scope_keeps_all_exact_occurrences_without_reference_labels():
    doc = source()
    before = copy.deepcopy(doc)
    state = p.state_for(doc, "A🙂")
    assert len(state["target_units"]) == 2
    assert "HIDDEN_QA" not in json.dumps(state)
    assert doc == before
    for unit in state["target_units"] + state["adjacent_non_target_units"]:
        field = next(f for f in doc["fields"] if f["name"] == unit["field"])
        assert field["text"][unit["start"] : unit["end"]] == unit["text"]
    with pytest.raises(ValueError):
        p.state_for(doc, "unseen")


def test_reverse_changes_only_option_order():
    state = p.state_for(source(), "A🙂")
    for engine in ("jev", "qwen"):
        a, b = [p.request_for(engine, state, rev) for rev in (False, True)]
        if engine == "qwen":
            a, b = [json.loads(r["messages"][1]["content"]) for r in (a, b)]
        assert a["state"] == b["state"]
        for key in p.QUESTIONS:
            x, y = [r["questions"][key]["criteria"] for r in (a, b)]
            assert x == y and list(x) == list(reversed(y))


@pytest.mark.parametrize("change", ["missing", "nan", "winner", "model", "shape"])
def test_invalid_jev_answers_rejected(change):
    value = jev_response()
    answer = value["answers"]["referent_kind"]
    if change == "missing":
        del answer["probabilities"]["other"]
    elif change == "nan":
        answer["probabilities"]["other"] = float("nan")
    elif change == "winner":
        answer["choice"] = "other"
    elif change == "model":
        value["model"] = "wrong"
    else:
        value["answers"]["referent_kind"] = []
    with pytest.raises(ValueError):
        p.parse("jev", value, p.helper_model(VERSION))


class Qwen:
    def call(self, path, body=None, timeout=15):
        if path == "/v1/models":
            return response({"data": [{"id": MODEL}]})
        if path == "/metrics":
            return response("llamacpp:requests_processing 0\nllamacpp:requests_deferred 0")
        return response(
            {
                "model": MODEL,
                "choices": [
                    {"finish_reason": "stop", "message": {"content": json.dumps(answers())}}
                ],
                "usage": {"prompt_tokens": 20, "completion_tokens": 5},
            }
        )


class Jev:
    def call(self, path, body=None, timeout=15):
        if path == "/v1/version":
            return response(VERSION)
        if path == "/metrics":
            return response("vllm:num_requests_running 0\nvllm:num_requests_waiting 0")
        return response(jev_response())


def fixture_run(tmp_path, monkeypatch, memory=10**8):
    monkeypatch.setattr(p, "SparkClient", Qwen)
    monkeypatch.setattr(p, "JevClient", Jev)
    monkeypatch.setattr(p, "memory_kib", lambda host: memory)
    monkeypatch.setattr(p.subprocess, "check_output", lambda *a, **kw: "unit")
    docs, targets = tmp_path / "docs.json", tmp_path / "targets.json"
    write_json(docs, [source()])
    write_json(targets, [{"id": "c0", "document_id": source()["document_id"], "query": "A🙂"}])
    output = tmp_path / "artifacts" / "triage"
    return output, p.run(tmp_path, [docs], targets, output)


def test_real_file_roundtrip_and_response_tamper_detection(tmp_path, monkeypatch):
    output, report = fixture_run(tmp_path, monkeypatch)
    assert report["submitted_calls"] == report["valid_responses"] == 6
    assert report["candidate_modified"] is report["graph_modified"] is False
    assert report["semantic_accuracy"] is None
    assert p.replay(output)["actual_model_calls"] == 0
    rows = json.loads((output / "rows.json").read_text())
    index = rows[0]["completion_rpc"]
    (output / f"rpc-{index:03}.response.bin").write_bytes(b"changed")
    with pytest.raises(ValueError, match="RPC changed"):
        p.replay(output)


def test_guard_retains_full_denominator_without_inference(tmp_path, monkeypatch):
    output, report = fixture_run(tmp_path, monkeypatch, memory=1)
    assert report["planned_calls"] == 6 and report["submitted_calls"] == 0
    assert len(json.loads((output / "rows.json").read_text())) == 6
    assert p.replay(output)["actual_model_calls"] == 0


def test_reversal_disagreement_separate_from_same_order_repeat():
    rows = [
        {
            "case_id": "c0",
            "engine": e,
            "status": "valid",
            "answer": {"choices": {k: "uncertain" if i != 1 else "other" for k in p.QUESTIONS}},
        }
        for e in ("qwen", "jev")
        for i in range(3)
    ]
    report = p.summarize([{"id": "c0"}], rows)
    for comparison in report["comparisons"]:
        assert comparison["qwen"]["same_order_repeat_stable"]
        assert not comparison["qwen"]["all_three_stable"]
        assert not comparison["stable_agreement"]
