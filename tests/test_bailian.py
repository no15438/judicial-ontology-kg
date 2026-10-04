"""Offline protocol and budget tests; metadata fixtures are not legal cases."""

import http.client
import json
import ssl
import subprocess
import urllib.error
from pathlib import Path

import pytest

from judicial_kg.extraction.bailian import (
    MODEL,
    BailianClient,
    NoRedirect,
    base_url,
    credentials,
    load_config,
    run_bailian,
)
from judicial_kg.extraction.transport import Response
from judicial_kg.sources.records import document

ROOT = Path(__file__).resolve().parents[1]
ENDPOINT = "https://workspace.cn-beijing.maas.aliyuncs.com/compatible-mode/v1"


@pytest.fixture
def workspace(tmp_path, monkeypatch):
    for name in (
        "ontology/private-lending-v0.1.json",
        "schemas/extraction-v0.1.schema.json",
        "prompts/qwen-extraction-v3.txt",
        "configs/bailian-extraction.json",
        "uv.lock",
    ):
        target = tmp_path / name
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes((ROOT / name).read_bytes())
    docs = [
        document(
            f"metadata-{i % 2}",
            {"record_pointer": f"/{i}"},
            {"field": (f"/{i}/field", "A B")},
            family_id=f"family:{i}",
            allocation="development_exposed",
            reference_labels=[{"answer": "HIDDEN_QA"}],
        )
        for i in range(3)
    ]
    path = tmp_path / "documents.json"
    path.write_text(json.dumps(docs))
    monkeypatch.setattr(
        subprocess, "check_output", lambda *a, **kw: "test-commit" if kw.get("text") else b""
    )
    return (
        tmp_path,
        [path],
        tmp_path / "configs/bailian-extraction.json",
        tmp_path / "artifacts/run",
    )


class SimulatedClient:
    def __init__(self, mode="ok"):
        self.mode, self.calls = mode, []

    def call(self, body, timeout):
        self.calls.append(body)
        assert "HIDDEN_QA" not in json.dumps(body)
        assert body["enable_thinking"] is False and body["enable_search"] is False
        if self.mode == "timeout":
            return Response(None, b"partial", 1, "transport_failed")
        if self.mode == "http":
            return Response(401, b'{"error":"unauthorized"}', 0.1)
        draft = {k: [] for k in ("actors", "mentions", "contexts", "roles", "events", "statements")}
        payload = {
            "model": MODEL,
            "choices": [{"finish_reason": "stop", "message": {"content": json.dumps(draft)}}],
            "usage": {"prompt_tokens": 200, "completion_tokens": 100, "total_tokens": 300},
        }
        if self.mode == "truncated":
            payload["choices"][0]["finish_reason"] = "length"
        if self.mode == "no_usage":
            del payload["usage"]
        if self.mode == "over_usage":
            payload["usage"]["prompt_tokens"] = 120001
        if self.mode == "bad_json":
            return Response(200, b"not JSON", 0.1)
        if self.mode == "interrupt":
            raise KeyboardInterrupt
        return Response(200, json.dumps(payload).encode(), 0.1)


def test_offline_plan_never_reads_credentials_or_calls_network(workspace, monkeypatch):
    monkeypatch.setattr(
        "judicial_kg.extraction.bailian.credentials",
        lambda *a: pytest.fail("Planning must not load keys"),
    )
    client = SimulatedClient()
    report = run_bailian(*workspace, client=client)
    assert not client.calls and report["requests_submitted"] == 0
    assert report["document_status_counts"] == {"planned_not_submitted": 2, "not_selected": 1}
    assert report["reserved_cny"] == "4.059648"
    assert report["semantic_precision_recall_f1"] is None


@pytest.mark.parametrize(
    "mode,status,calls",
    [
        ("ok", "accepted_empty", 2),
        ("truncated", "response_truncated", 2),
        ("timeout", "transport_failed", 1),
        ("http", "http_failed", 1),
        ("no_usage", "usage_missing", 1),
        ("bad_json", "protocol_invalid_response", 1),
        ("over_usage", "provider_usage_exceeds_reservation", 1),
    ],
)
def test_run_counts_failures_preserves_bytes_and_never_retries(workspace, mode, status, calls):
    client = SimulatedClient(mode)
    report = run_bailian(*workspace, execute=True, client=client)
    assert len(client.calls) == calls == report["requests_submitted"]
    assert report["selected_outcomes"][0]["status"] == status
    assert report["expected_documents"] == 3 and report["selected_documents"] == 2
    assert report["validation"]["documents_missing"] == (1 if mode == "ok" else 3)
    if calls == 1:
        assert report["selected_outcomes"][1]["status"] == "not_attempted_after_failure"
    assert report["completed"]
    assert len(list(workspace[3].glob("response-*.bin"))) == calls
    with pytest.raises(FileExistsError):
        run_bailian(*workspace, execute=True, client=client)


def test_budget_is_reserved_before_requests_and_not_released_on_failure(workspace):
    config_path = workspace[2]
    config = json.loads(config_path.read_text())
    config["budget_cny"] = "2.1"
    config_path.write_text(json.dumps(config))
    client = SimulatedClient("truncated")
    report = run_bailian(*workspace, execute=True, client=client)
    assert len(client.calls) == 1
    assert report["selected_outcomes"][1]["status"] == "budget_exceeded"
    assert report["reserved_cny"] == "2.029824"


def test_interrupt_keeps_inflight_checkpoint(workspace):
    with pytest.raises(KeyboardInterrupt):
        run_bailian(*workspace, execute=True, client=SimulatedClient("interrupt"))
    report = json.loads((workspace[3] / "report.json").read_text())
    assert not report["completed"] and report["requests_submitted"] == 1
    assert report["selected_outcomes"][0]["status"] == "in_flight"


def test_large_input_is_not_truncated_or_sent(workspace):
    path = workspace[1][0]
    value = document(
        "metadata",
        {"record_pointer": "/0"},
        {"field": ("/0/field", "X" * 120000)},
        family_id="family:0",
        allocation="development_exposed",
    )
    path.write_text(json.dumps([value]))
    client = SimulatedClient()
    report = run_bailian(*workspace, execute=True, client=client)
    assert not client.calls
    assert report["document_status_counts"] == {"input_reservation_exceeded": 1}


def test_config_rejects_budget_and_model_expansion(tmp_path):
    path = tmp_path / "config.json"
    for key, value in (
        ("budget_cny", "NaN"),
        ("budget_cny", "5.01"),
        ("max_documents", 3),
        ("model", "other"),
        ("input_cny_per_million", "0"),
    ):
        config = json.loads((ROOT / "configs/bailian-extraction.json").read_text())
        config[key] = value
        path.write_text(json.dumps(config))
        with pytest.raises(ValueError):
            load_config(path)


def test_credentials_never_eval_shell_and_require_private_file(tmp_path, monkeypatch):
    monkeypatch.delenv("DASHSCOPE_API_KEY", raising=False)
    monkeypatch.delenv("BAILIAN_BASE_URL", raising=False)
    path = tmp_path / ".env.local"
    path.write_text("DASHSCOPE_API_KEY=sk-unit-test-only\nBAILIAN_BASE_URL=" + ENDPOINT + "\n")
    path.chmod(0o644)
    with pytest.raises(ValueError, match="private"):
        credentials(tmp_path)
    path.chmod(0o600)
    assert credentials(tmp_path) == ("sk-unit-test-only", ENDPOINT)
    path.write_text("DASHSCOPE_API_KEY=$(touch should-not-exist)\nBAILIAN_BASE_URL=" + ENDPOINT)
    with pytest.raises(ValueError):
        credentials(tmp_path)
    assert not (tmp_path / "should-not-exist").exists()


def test_transport_limits_endpoint_and_prevents_redirect():
    for url in (
        "http://example.com",
        ENDPOINT + "?key=private",
        ENDPOINT + "/",
        ENDPOINT + "@evil",
    ):
        with pytest.raises(ValueError):
            base_url(url)
    assert NoRedirect().redirect_request(None, None, 302, "", {}, "https://example.com") is None
    client = BailianClient("sk-unit-test-only", ENDPOINT)

    class Opener:
        def open(self, req, timeout):
            assert req.full_url == ENDPOINT + "/chat/completions"
            assert req.get_header("Authorization") == "Bearer sk-unit-test-only"
            assert b"sk-unit-test-only" not in req.data
            import io

            raise urllib.error.HTTPError(req.full_url, 429, "rate limit", {}, io.BytesIO(b"error"))

    client._opener = Opener()
    result = client.call({"messages": []}, 10)
    assert result.status == 429 and result.raw == b"error" and result.error is None


def test_partial_http_response_remains_a_failure():
    class Opener:
        def open(self, *args, **kwargs):
            raise http.client.IncompleteRead(b"partial response", 100)

    client = BailianClient("sk-unit-test-only", ENDPOINT)
    client._opener = Opener()
    result = client.call({"messages": []}, 10)
    assert result.error == "transport_failed" and result.raw == b"partial response"


def test_tls_failure_is_classified_without_logging_key_or_exception_text():
    class Opener:
        def open(self, *args, **kwargs):
            raise urllib.error.URLError(ssl.SSLCertVerificationError("private transport details"))

    client = BailianClient("sk-unit-test-only", ENDPOINT)
    client._opener = Opener()
    result = client.call({"messages": []}, 10)
    assert result.error == "tls_verification_failed" and result.raw == b""
