"""Staged protocol checks on metadata fixtures, never judicial gold."""

import copy
import json
from pathlib import Path

import pytest
from test_actor_grounded import nested
from test_anchored import PROVENANCE, source
from test_extraction import MODEL, SimulatedClient, response, workspace  # noqa: F401

from judicial_kg.contracts import Contract
from judicial_kg.extraction import context_first as protocol
from judicial_kg.extraction.draft import DraftError
from judicial_kg.extraction.replay import replay_completed
from judicial_kg.extraction.run import run_extraction
from judicial_kg.extraction.transport import Response
from judicial_kg.sources.records import canonical_hash, sha256

ROOT = Path(__file__).resolve().parents[1]
CONTRACT = Contract.load(ROOT, "0.2.0")


def fixtures():
    draft = nested()[1]

    def explicit(node):
        if isinstance(node, dict):
            if "occurrence" in node and node["occurrence"] is None:
                node["occurrence"] = 0
            for v in node.values():
                explicit(v)
        elif isinstance(node, list):
            for v in node:
                explicit(v)

    explicit(draft)
    body = {k: copy.deepcopy(draft["contexts"][0][k]) for k in protocol.CHILDREN}
    for c in draft["contexts"]:
        for k in protocol.CHILDREN:
            c[k] = []
    return draft, body


def test_frozen_scope_and_catalog_exclude_other_source_units_and_qa():
    header, body = fixtures()
    original = canonical_hash(header)
    schema = protocol.body_schema(header, header["contexts"][0], CONTRACT)
    assert schema["$defs"]["selector"]["properties"]["unit_id"]["enum"] == ["u0", "u1"]
    messages = protocol.body_messages(source(), "prompt", header, header["contexts"][0], schema)
    payload = json.loads(messages[-1]["content"])
    assert [u["id"] for u in payload["source_units"]] == ["u0", "u1"]
    assert "HIDDEN_QA" not in json.dumps(messages)
    assert canonical_hash(header) == original
    for mutation in (
        lambda b: b["roles"][0].update(actor_id="foreign"),
        lambda b: b["events"][0].update(evidence_units=["u3"]),
        lambda b: b["events"][0]["statements"][0]["value"]["source"].update(unit_id="u3"),
        lambda b: b.update(attributed_actor_ids=[]),
    ):
        changed = copy.deepcopy(body)
        mutation(changed)
        with pytest.raises(DraftError):
            protocol.check_schema(changed, schema)


def test_child_ids_namespaced_without_changing_assertions_or_header():
    header, body = fixtures()
    before = copy.deepcopy(header)
    combined = protocol.merge_body(header, 0, body)
    assert header == before and combined["actors"] == header["actors"]
    assert combined["contexts"][0]["events"][0]["id"] == "v10_b0_e"
    assert (
        combined["contexts"][0]["events"][0]["evidence_units"]
        == body["events"][0]["evidence_units"]
    )
    duplicate = copy.deepcopy(body)
    duplicate["roles"][0]["id"] = duplicate["events"][0]["id"]
    with pytest.raises(DraftError):
        protocol.merge_body(header, 0, duplicate)


@pytest.mark.parametrize("bad", [None, 9, True])
def test_bad_occurrence_stops_before_body(bad):
    header, _ = fixtures()
    header["actors"][0]["mentions"][0]["evidence"]["occurrence"] = bad
    calls = []

    def infer(messages, fmt, key):
        calls.append(key)
        return json.dumps(header)

    with pytest.raises(DraftError):
        protocol.extract(source(), "p", CONTRACT, PROVENANCE, infer, lambda *a: None)
    assert calls == ["header"]


def prepare(workspace_data):
    root, docs, config_path = workspace_data
    for name in (
        "ontology/private-lending-v0.2.json",
        "schemas/extraction-v0.2.schema.json",
        "prompts/qwen-extraction-v10.txt",
    ):
        (root / name).write_bytes((ROOT / name).read_bytes())
    docs.write_text(json.dumps([source()]))
    cfg = json.loads(config_path.read_text())
    cfg.update(version="qwen-extraction-v10", max_documents=1)
    config_path.write_text(json.dumps(cfg))
    return root, docs, config_path


class StagedClient(SimulatedClient):
    def __init__(self, failure=None):
        super().__init__()
        self.completions, self.failure = 0, failure
        self.metrics = 0

    def call(self, path, body=None, timeout=15):
        if path == "/metrics":
            self.metrics += 1
            if self.failure == "busy" and self.metrics == 2:
                self.paths.append(path)
                return Response(
                    200, b"llamacpp:requests_processing 1\nllamacpp:requests_deferred 0\n", 0.01
                )
        if path == "/tokenize" and self.failure == "budget" and self.completions == 1:
            self.paths.append(path)
            return response({"tokens": [1] * 32760})
        if path != "/v1/chat/completions":
            return super().call(path, body, timeout)
        self.paths.append(path)
        self.completions += 1
        assert "schema" in body["response_format"]
        header, children = fixtures()
        if self.completions == 1:
            content = header
            if self.failure == "header":
                content["actors"][0]["mentions"][0]["evidence"]["text"] = "missing"
        else:
            content = children
            if self.failure == "child":
                content["events"][0]["evidence_units"] = ["u3"]
        return response(
            {
                "model": MODEL,
                "choices": [
                    {
                        "finish_reason": "length"
                        if self.failure == "truncated" and self.completions == 2
                        else "stop",
                        "message": {"content": json.dumps(content)},
                    }
                ],
                "usage": {"prompt_tokens": 100, "completion_tokens": 50, "total_tokens": 150},
            }
        )


@pytest.mark.parametrize(
    "failure,count,status",
    [
        (None, 2, "accepted_candidate"),
        ("header", 1, "draft_grounding_failed"),
        ("child", 2, "draft_grounding_failed"),
        ("busy", 1, "service_busy"),
        ("budget", 1, "context_over_budget"),
        ("truncated", 2, "response_truncated"),
    ],
)
def test_every_phase_is_bounded_and_full_failure_replays(workspace, failure, count, status):  # noqa: F811
    root, docs, config_path = prepare(workspace)
    run = root / "artifacts/staged"
    client = StagedClient(failure)
    report = run_extraction(root, [docs], config_path, run, client)
    assert report["requests_submitted"] == count
    assert report["document_status_counts"] == {status: 1}
    assert len(json.loads((run / "accepted.json").read_text())) == int(failure is None)
    if failure != "header":
        plan = json.loads((run / "stage-000-stage-plan.json").read_text())
        assert plan["complete"] == (failure is None)
        assert plan["contexts"][0]["status"] == ("validated" if failure is None else "failed")
    saved = replay_completed(root, [docs], run, root / "artifacts/replay")
    assert saved["requests_submitted"] == 0 and saved["saved_requests_replayed"] == count
    assert saved["document_status_counts"] == report["document_status_counts"]
    assert saved["outcome_and_candidate_equality_verified"]


@pytest.mark.parametrize("tamper", ["source", "response", "request", "header"])
def test_replay_refuses_changed_source_response_request_and_header(workspace, tamper):  # noqa: F811
    root, docs, config_path = prepare(workspace)
    run = root / "artifacts/staged"
    run_extraction(root, [docs], config_path, run, StagedClient())
    report = json.loads((run / "report.json").read_text())
    if tamper == "source":
        docs.write_text(docs.read_text() + " ")
    else:
        call = next(c for c in report["response_metadata"] if c["path"] == "/v1/chat/completions")
        name = f"rpc-{call['rpc']:03}.{'request.json' if tamper == 'request' else 'response.bin'}"
        if tamper == "header":
            name = "stage-000-header-draft.json"
        path = run / name
        value = json.loads(path.read_bytes())
        if tamper == "request":
            value["body"]["messages"][0]["content"] += "changed"
            path.write_text(json.dumps(value))
            # Even a self-consistent receipt cannot change source-derived messages.
            report["outputs_sha256"][name] = sha256(path.read_bytes())
            (run / "report.json").write_text(json.dumps(report))
        else:
            path.write_text(json.dumps(value) + " ")
    with pytest.raises((ValueError, RuntimeError)):
        replay_completed(root, [docs], run, root / "artifacts/replay")
