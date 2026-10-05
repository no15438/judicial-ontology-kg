"""Metadata-only probes verify isolation, fail-closed guards and offline replay."""

import copy
import json

import pytest
from test_anchored import source

from judicial_kg.extraction import jev_shadow as p
from judicial_kg.extraction.transport import Response
from judicial_kg.sources.development import write_json


def header():
    return {
        "contexts": [
            {"id": "PRIVATE_OLD_LABEL", "kind": "court_finding", "assertion_units": ["u0", "u2"]},
            {"id": "other", "kind": "procedural_record", "assertion_units": ["u1"]},
        ]
    }


def test_state_keeps_source_spans_but_never_qa_old_label_or_context_id():
    before = copy.deepcopy(header())
    cases = p.cases_from_header(source(), before)
    assert before == header()
    for case in cases:
        for engine in ("qwen", "jev"):
            payload = json.dumps(p.request_for(engine, case["state"]))
            assert "HIDDEN_QA" not in payload
            assert "PRIVATE_OLD_LABEL" not in payload
            assert "baseline" not in payload
    state = cases[0]["state"]
    assert [u["id"] for u in state["target_units"]] == ["u0", "u2"]
    assert "u1" in [u["id"] for u in state["adjacent_non_target_units"]]
    for unit in state["target_units"]:
        text = next(f["text"] for f in source()["fields"] if f["name"] == unit["field"])
        assert text[unit["start"] : unit["end"]] == unit["text"]


@pytest.mark.parametrize("ids", [["u999"], ["u0", "u0"]])
def test_bad_scope_rejected(ids):
    h = header()
    h["contexts"][0]["assertion_units"] = ids
    with pytest.raises(ValueError):
        p.cases_from_header(source(), h)


def test_order_reversal_preserves_option_meanings_and_source():
    state = p.cases_from_header(source(), header())[0]["state"]
    a, b = [p.request_for("jev", state, rev) for rev in (False, True)]
    x, y = [v["questions"]["attribution"]["criteria"] for v in (a, b)]
    assert x == y
    assert list(x) == list(reversed(y))
    assert a["state"] == b["state"]
    a, b = [p.request_for("qwen", state, rev) for rev in (False, True)]
    assert a["response_format"]["schema"]["properties"]["choice"]["enum"] == list(p.OPTIONS)
    assert b["response_format"]["schema"]["properties"]["choice"]["enum"] == list(
        reversed(p.OPTIONS)
    )


def jev_response():
    return {
        "model": "pinned",
        "answers": {
            "attribution": {
                "type": "choice",
                "choice": "uncertain",
                "probabilities": {k: float(k == "uncertain") for k in p.OPTIONS},
            }
        },
        "usage": {"input_tokens": 21, "output_tokens": 0},
    }


@pytest.mark.parametrize("change", ["missing", "nan", "negative", "sum", "winner", "model"])
def test_invalid_probabilities_or_model_fail_closed(change):
    value = jev_response()
    probs = value["answers"]["attribution"]["probabilities"]
    if change == "missing":
        del probs["mixed"]
    elif change == "nan":
        probs["mixed"] = float("nan")
    elif change == "negative":
        probs["mixed"] = -0.1
    elif change == "sum":
        probs["mixed"] = 0.5
    elif change == "winner":
        value["answers"]["attribution"]["choice"] = "mixed"
    else:
        value["model"] = "unrecorded"
    with pytest.raises(ValueError):
        p.parse_answer("jev", value, "pinned")


@pytest.mark.parametrize(
    "metrics",
    [
        "",
        "vllm:num_requests_running 0",
        "vllm:num_requests_running NaN\nvllm:num_requests_waiting 0",
    ],
)
def test_missing_or_nonfinite_load_rejected(metrics):
    with pytest.raises(ValueError):
        p.jev_busy(metrics)


def test_load_sums_all_engines():
    assert p.jev_busy(
        'vllm:num_requests_running{engine="0"} 0\n'
        'vllm:num_requests_running{engine="1"} 1\nvllm:num_requests_waiting 0'
    )


@pytest.mark.parametrize(
    "path,body", [("/restart", None), ("/v1/systemone", None), ("/v1/version", {})]
)
def test_jev_routes_restricted(path, body):
    with pytest.raises(ValueError):
        p.JevClient().call(path, body)


VERSION = {
    "model_dir": "unit",
    "T": 0.85,
    "noul_t": 1.8,
    "noul_bias": 0.0,
    "flags": {"perms": 1},
    "shim_file": "shim.py",
    "shim_sha256": "a" * 64,
}
MODEL = "qwen3.8-flash-next-iq4xs"


def response(value):
    raw = value.encode() if isinstance(value, str) else json.dumps(value).encode()
    return Response(200, raw, 0.01)


class FakeQwen:
    def __init__(self, host):
        pass

    def call(self, path, body=None, timeout=15):
        if path == "/health":
            return response({"status": "ok"})
        if path == "/v1/models":
            return response({"data": [{"id": MODEL}]})
        if path == "/metrics":
            return response("llamacpp:requests_processing 0\nllamacpp:requests_deferred 0")
        assert path == "/v1/chat/completions"
        return response(
            {
                "model": MODEL,
                "choices": [
                    {"finish_reason": "stop", "message": {"content": '{"choice":"uncertain"}'}}
                ],
                "usage": {"prompt_tokens": 20, "completion_tokens": 5},
            }
        )


class FakeJev:
    def __init__(self, host):
        pass

    def call(self, path, body=None, timeout=15):
        if path == "/v1/version":
            return response(VERSION)
        if path == "/v1/models":
            return response({"data": [{"id": "qwen"}]})
        if path == "/metrics":
            return response("vllm:num_requests_running 0\nvllm:num_requests_waiting 0")
        assert path == "/v1/systemone"
        value = jev_response()
        value["model"] = p.helper_model(VERSION)
        return response(value)


def run_fixture(tmp_path, monkeypatch, mem=10**8):
    monkeypatch.setattr(p, "SparkClient", FakeQwen)
    monkeypatch.setattr(p, "JevClient", FakeJev)
    monkeypatch.setattr(p, "memory_kib", lambda host: mem)
    # Only git metadata calls are substituted; all request/response files are real.
    monkeypatch.setattr(
        p.subprocess, "check_output", lambda *a, **kw: "unit" if kw.get("text") else b""
    )
    documents, h = tmp_path / "documents.json", tmp_path / "header.json"
    write_json(documents, [source()])
    write_json(h, header())
    output = tmp_path / "artifacts" / "shadow"
    report = p.run(tmp_path, [documents], h, source()["document_id"], output)
    return output, report


def test_shadow_run_and_replay_never_accept_or_modify_candidates(tmp_path, monkeypatch):
    output, report = run_fixture(tmp_path, monkeypatch)
    assert report["model_calls_submitted"] == 8
    assert report["status_counts"] == {"valid": 8}
    assert all(c["review_required"] for c in report["comparisons"])
    assert report["semantic_accuracy"] is None
    assert report["candidate_modified"] is False
    assert p.replay(output)["model_calls"] == 0
    assert not (output / "accepted.json").exists()


def test_low_memory_stops_all_calls_but_keeps_denominator(tmp_path, monkeypatch):
    output, report = run_fixture(tmp_path, monkeypatch, mem=1)
    assert report["model_calls_submitted"] == 0
    assert report["status_counts"] == {
        "guard_failed": 1,
        "not_attempted_after_guard_or_transport_failure": 7,
    }
    assert p.replay(output)["cases"] == 2


@pytest.mark.parametrize(
    "file", ["cases.json", "header.json", "source-document.json", "rpc-007.response.bin"]
)
def test_replay_rejects_changed_source_and_responses(tmp_path, monkeypatch, file):
    output, _ = run_fixture(tmp_path, monkeypatch)
    target = output / file
    if file.endswith(".bin"):
        target.write_bytes(b"changed")
    else:
        value = json.loads(target.read_text())
        if file == "cases.json":
            value[0]["baseline"] = "mixed"
        elif file == "header.json":
            value["contexts"][0]["kind"] = "mixed"
        else:
            value["fields"][0]["text"] += "X"
        target.write_text(json.dumps(value))
    with pytest.raises(ValueError):
        p.replay(output)
