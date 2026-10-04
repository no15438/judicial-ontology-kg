"""Transport simulations and metadata fixtures; no synthetic legal cases or gold."""

import base64
import json
import subprocess
from pathlib import Path

import pytest

from judicial_kg.contracts import Contract
from judicial_kg.extraction.draft import (
    DraftError,
    draft_schema,
    materialize,
    messages,
    resolve_quote,
)
from judicial_kg.extraction.run import busy, completion_content, load_config, run_extraction
from judicial_kg.extraction.transport import Response, SparkClient
from judicial_kg.sources.records import document
from judicial_kg.validation import validate_record

ROOT = Path(__file__).resolve().parents[1]
MODEL = "qwen3.8-flash-next-iq4xs"


def source(index=0):
    return document(
        "metadata",
        {"record_pointer": f"/{index}"},
        {"field": (f"/{index}/field", "A🙂 A🙂 B")},
        family_id=f"family:{index}",
        allocation="development_exposed",
        reference_labels=[{"answer": "HIDDEN_QA"}],
    )


def draft():
    return {
        "actors": [{"id": "a", "actor_type": "unknown"}],
        "mentions": [
            {
                "id": "m",
                "actor_id": "a",
                "kind": "description",
                "evidence": {"field": "field", "quote": "B", "occurrence": None},
            }
        ],
        "contexts": [],
        "roles": [],
        "events": [],
        "statements": [],
    }


def response(payload, status=200, error=None):
    return Response(status, json.dumps(payload).encode(), 0.01, error)


def completion(content=None):
    return {
        "model": MODEL,
        "choices": [
            {
                "finish_reason": "stop",
                "message": {"content": json.dumps(draft()) if content is None else content},
            }
        ],
        "usage": {"prompt_tokens": 100, "completion_tokens": 50, "total_tokens": 150},
    }


def test_quotes_use_exact_unicode_positions_and_do_not_pick_first_duplicate():
    doc = source()
    with pytest.raises(DraftError, match="quote_ambiguous"):
        resolve_quote(doc, {"field": "field", "quote": "A🙂", "occurrence": None})
    span = resolve_quote(doc, {"field": "field", "quote": "A🙂", "occurrence": 1})
    assert (span["start"], span["end"], span["quote"]) == (3, 5, "A🙂")
    for quote, occurrence in [("A 🙂", None), ("A🙂", 9), ("A🙂", True)]:
        with pytest.raises(DraftError):
            resolve_quote(doc, {"field": "field", "quote": quote, "occurrence": occurrence})


def test_materialization_fills_only_trusted_provenance_and_passes_contract():
    contract, doc = Contract.load(ROOT), source()
    provenance = {
        "method": "model",
        "producer": "test-simulation",
        "version": "unit",
        "run_id": "unit",
    }
    result = materialize(json.dumps(draft()), doc, contract, provenance)
    assert result["document_id"] == doc["document_id"]
    assert result["provenance"] == provenance
    assert result["actors"][0]["mention_ids"] == ["m"]
    assert validate_record(result, doc, contract) == []
    polluted = draft() | {"reference_status": "gold"}
    with pytest.raises(DraftError, match="draft_schema_invalid"):
        materialize(json.dumps(polluted), doc, contract, provenance)
    with pytest.raises(DraftError, match="draft_invalid_json"):
        materialize("```json\n{}\n```", doc, contract, provenance)


def test_inverse_mentions_are_not_model_generated_or_silently_deduplicated():
    contract = Contract.load(ROOT)
    value = draft()
    value["actors"][0]["mention_ids"] = ["made_up"]
    with pytest.raises(DraftError, match="draft_schema_invalid"):
        materialize(json.dumps(value), source(), contract, {})
    value = draft()
    repeated = json.loads(json.dumps(value["mentions"][0]))
    repeated["id"] = "m2"
    value["mentions"].append(repeated)
    with pytest.raises(DraftError, match="duplicate_mention_evidence"):
        materialize(json.dumps(value), source(), contract, {})


def test_prompt_cannot_leak_qa_answers_or_reference_labels():
    msg = messages(source(), "instructions", draft_schema(Contract.load(ROOT)))
    assert "HIDDEN_QA" not in json.dumps(msg)
    assert json.loads(msg[1]["content"]) == {"fields": [{"name": "field", "text": "A🙂 A🙂 B"}]}


@pytest.mark.parametrize(
    "mutation,code",
    [
        (lambda p: p.update(model="other"), "response_model_mismatch"),
        (lambda p: p["choices"][0].update(finish_reason="length"), "response_truncated"),
        (lambda p: p["choices"][0].update(finish_reason=None), "response_not_stopped"),
        (lambda p: p["choices"][0]["message"].update(content=""), "response_content_empty"),
        (
            lambda p: p["choices"][0]["message"].update(refusal="no"),
            "response_refusal_or_tool_call",
        ),
        (lambda p: p.update(choices=[]), "response_choices_invalid"),
    ],
)
def test_bad_completion_not_silently_salvaged(mutation, code):
    payload = completion()
    mutation(payload)
    with pytest.raises(DraftError, match=code):
        completion_content(payload, MODEL)


def test_ssh_body_goes_over_stdin_and_http_error_bytes_are_retained(monkeypatch):
    raw = b'{"error":"protocol probe"}'

    def fake_run(argv, **kwargs):
        assert "PRIVATE_METADATA" not in " ".join(argv)
        request = json.loads(kwargs["input"])
        assert request["body"] == {"content": "PRIVATE_METADATA"}
        assert "StrictHostKeyChecking=yes" in argv
        return subprocess.CompletedProcess(
            argv,
            0,
            json.dumps({"status": 400, "body_base64": base64.b64encode(raw).decode()}).encode(),
            b"",
        )

    monkeypatch.setattr(subprocess, "run", fake_run)
    result = SparkClient().call("/tokenize", {"content": "PRIVATE_METADATA"})
    assert result.status == 400 and result.raw == raw
    with pytest.raises(ValueError):
        result.json()


def test_ssh_timeout_keeps_partial_output(monkeypatch):
    def fake_run(argv, **kwargs):
        raise subprocess.TimeoutExpired(argv, 20, output=b"partial", stderr=b"diagnostic")

    monkeypatch.setattr(subprocess, "run", fake_run)
    result = SparkClient().call("/health")
    assert result.error == "ssh_timeout" and result.raw == b"partial"
    assert result.diagnostics == b"diagnostic"
    with pytest.raises(ValueError):
        SparkClient("spark-lan;command")
    with pytest.raises(ValueError):
        SparkClient(port=443)


def test_busy_requires_both_metrics_and_never_treats_missing_as_idle():
    assert busy("llamacpp:requests_processing 1\nllamacpp:requests_deferred 0\n")
    assert not busy("llamacpp:requests_processing 0\nllamacpp:requests_deferred 0\n")
    with pytest.raises(ValueError):
        busy("# no metrics")


@pytest.fixture
def workspace(tmp_path, monkeypatch):
    for filename in (
        "ontology/private-lending-v0.1.json",
        "schemas/extraction-v0.1.schema.json",
        "prompts/qwen-extraction-v3.txt",
        "configs/qwen-extraction.json",
        "uv.lock",
    ):
        path = tmp_path / filename
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes((ROOT / filename).read_bytes())
    config_path = tmp_path / "configs/qwen-extraction.json"
    config = json.loads(config_path.read_text())
    config["limit_per_source"] = 2
    config_path.write_text(json.dumps(config))
    docs_path = tmp_path / "documents.json"
    docs_path.write_text(json.dumps([source(i) for i in range(3)]))
    monkeypatch.setattr(
        subprocess, "check_output", lambda *a, **kw: "test-commit" if kw.get("text") else b""
    )
    return tmp_path, docs_path, config_path


class SimulatedClient:
    """A protocol test double, never used for real extraction or reported as Qwen."""

    def __init__(self, mode="ok"):
        self.mode, self.paths = mode, []

    def call(self, path, body=None, timeout=15):
        self.paths.append(path)
        if path == "/health":
            return response({"status": "ok"})
        if path == "/v1/models":
            return response({"data": [{"id": MODEL}]})
        if path == "/props":
            return response(
                {
                    "default_generation_settings": {"n_ctx": 32768},
                    "build_info": "test",
                    "total_slots": 1,
                }
            )
        if path == "/metrics":
            return Response(
                200, b"llamacpp:requests_processing 0\nllamacpp:requests_deferred 0\n", 0.01
            )
        if path == "/apply-template":
            return response({"prompt": "metadata"})
        if path == "/tokenize":
            return response({"tokens": [1] * (30000 if self.mode == "oversize" else 100)})
        assert path == "/v1/chat/completions"
        assert body["response_format"] == {"type": "json_object"}
        assert "JSON Schema:" in body["messages"][0]["content"]
        if self.mode == "timeout":
            return Response(None, b"partial", 1, "ssh_timeout")
        if self.mode == "interrupt":
            raise KeyboardInterrupt
        payload = completion()
        if self.mode == "truncated":
            payload["choices"][0]["finish_reason"] = "length"
        if self.mode == "bad_quote":
            value = draft()
            value["mentions"][0]["evidence"]["quote"] = "MISSING"
            payload["choices"][0]["message"]["content"] = json.dumps(value)
        if self.mode == "invalid_contract":
            value = draft()
            value["mentions"][0]["actor_id"] = "missing"
            payload["choices"][0]["message"]["content"] = json.dumps(value)
        return response(payload)


@pytest.mark.parametrize(
    "mode,status,submitted",
    [
        ("ok", "accepted_candidate", 2),
        ("oversize", "context_over_budget", 0),
        ("truncated", "response_truncated", 2),
        ("bad_quote", "quote_missing", 2),
        ("invalid_contract", "contract_rejected", 2),
    ],
)
def test_run_preserves_denominator_and_each_failed_attempt(workspace, mode, status, submitted):
    root, docs, config = workspace
    client = SimulatedClient(mode)
    output = root / "artifacts/run"
    report = run_extraction(root, [docs], config, output, client)
    assert report["completed"] and report["requests_submitted"] == submitted
    assert report["expected_documents"] == 3 and report["selected_documents"] == 2
    assert report["document_status_counts"] == {status: 2, "not_selected": 1}
    assert report["semantic_precision_recall_f1"] is None
    assert report["validation"]["documents_missing"] == (
        1 if mode in ("ok", "invalid_contract") else 3
    )
    if submitted:
        assert any(p.read_bytes() for p in output.glob("*.response.bin"))
    if mode == "invalid_contract":
        assert report["validation"]["documents_rejected"] == 2
    if mode == "truncated":
        assert report["selected_outcomes"][0]["usage"]["completion_tokens"] == 50
    with pytest.raises(FileExistsError):
        run_extraction(root, [docs], config, output, client)


def test_timeout_halts_later_submissions(workspace):
    root, docs, config = workspace
    client = SimulatedClient("timeout")
    report = run_extraction(root, [docs], config, root / "artifacts/run", client)
    assert report["requests_submitted"] == 1
    assert client.paths.count("/v1/chat/completions") == 1
    assert report["document_status_counts"]["not_attempted_after_transport_failure"] == 1


def test_interruption_leaves_checkpoint_without_claiming_completion(workspace):
    root, docs, config = workspace
    output = root / "artifacts/run"
    with pytest.raises(KeyboardInterrupt):
        run_extraction(root, [docs], config, output, SimulatedClient("interrupt"))
    report = json.loads((output / "report.json").read_text())
    assert not report["completed"] and report["requests_submitted"] == 1
    assert report["document_status_counts"]["in_flight"] == 1


def test_invalid_config_and_blind_test_abort_before_network(workspace):
    root, docs, config = workspace
    altered = json.loads(config.read_text())
    altered["max_documents"] = 1000
    config.write_text(json.dumps(altered))
    with pytest.raises(ValueError):
        load_config(config)
    config.write_bytes((ROOT / "configs/qwen-extraction.json").read_bytes())
    value = source()
    value["allocation"] = "test"
    docs.write_text(json.dumps([value]))
    client = SimulatedClient()
    with pytest.raises(ValueError, match="development"):
        run_extraction(root, [docs], config, root / "artifacts/run", client)
    assert client.paths == []


def test_malformed_health_response_is_recorded_for_all_selected(workspace):
    root, docs, config = workspace

    class MalformedClient:
        def call(self, path, body=None, timeout=15):
            assert path == "/health"
            return response([])

    report = run_extraction(root, [docs], config, root / "artifacts/run", MalformedClient())
    assert report["document_status_counts"] == {"not_selected": 1, "preflight_failed": 2}
    assert report["requests_submitted"] == 0
