"""Offline replay preserves provenance and denominators, with no model requests."""

import json

import pytest
from test_extraction import SimulatedClient, completion, response, workspace  # noqa: F401

from judicial_kg.extraction.replay import replay_completed
from judicial_kg.extraction.run import run_extraction
from judicial_kg.extraction.transport import SparkClient


@pytest.fixture
def saved_run(request):
    root, docs, config = request.getfixturevalue("workspace")
    value = json.loads(config.read_text())
    value["version"] = "qwen-extraction-v5"
    config.write_text(json.dumps(value))

    class Client(SimulatedClient):
        def call(self, path, body=None, timeout=15):
            if path != "/v1/chat/completions":
                return super().call(path, body, timeout)
            return response(completion('{"actors":[],"mentions":[],"contexts":[]}'))

    saved = root / "artifacts/original"
    run_extraction(root, [docs], config, saved, Client())
    return root, docs, saved


def test_offline_replay_never_calls_model_or_overwrites_original(saved_run, monkeypatch):
    root, docs, saved = saved_run
    original = {p.name: p.read_bytes() for p in saved.iterdir()}

    def forbidden(*args, **kwargs):
        pytest.fail("Offline replay must never contact a model")

    monkeypatch.setattr(SparkClient, "call", forbidden)
    output = root / "artifacts/replay"
    report = replay_completed(root, [docs], saved, output)
    assert report["requests_submitted"] == 0
    assert report["document_status_counts"] == {"accepted_empty": 2, "not_selected": 1}
    assert report["validation"]["documents_missing"] == 1
    assert {p.name: p.read_bytes() for p in saved.iterdir()} == original
    with pytest.raises(FileExistsError):
        replay_completed(root, [docs], saved, output)


@pytest.mark.parametrize("change", ["denominator", "response", "source_binding"])
def test_replay_rejects_changed_inputs_and_unbound_responses(saved_run, change):
    root, docs, saved = saved_run
    if change == "denominator":
        docs.write_text(json.dumps(json.loads(docs.read_text())[:2]))
    elif change == "response":
        (saved / "rpc-007.response.bin").write_text("{}")
    else:
        # Even a matching artifact hash must not bind a different source request.
        from judicial_kg.sources.records import sha256

        path = saved / "rpc-007.request.json"
        request = json.loads(path.read_text())
        request["body"]["messages"][-1]["content"] = "{}"
        path.write_text(json.dumps(request))
        report_path = saved / "report.json"
        report = json.loads(report_path.read_text())
        report["outputs_sha256"][path.name] = sha256(path.read_bytes())
        report_path.write_text(json.dumps(report))
    output = root / "artifacts/replay"
    with pytest.raises(ValueError):
        replay_completed(root, [docs], saved, output)
    assert not output.exists()
