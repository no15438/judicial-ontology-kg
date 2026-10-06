"""Controller tests use synthetic requests; they make no semantic extraction claim."""

import json
from decimal import Decimal

import pytest
from test_context_first import prepare
from test_extraction import response, workspace  # noqa: F401
from test_scoped import ROOT

from judicial_kg.extraction import bailian_staged as cloud
from judicial_kg.extraction.draft import DraftError
from judicial_kg.sources.records import canonical_hash


@pytest.fixture
def setup(workspace, monkeypatch):  # noqa: F811
    root, docs, _ = prepare(workspace)
    for name in [
        "ontology/private-lending-v0.3.json",
        "schemas/extraction-v0.3.schema.json",
        "prompts/qwen-extraction-v16.txt",
    ]:
        (root / name).write_bytes((ROOT / name).read_bytes())
    variant = {"schema": False, "request": False}

    def extract(doc, prompt, contract, provenance, infer, save):
        chain = []
        for stage in ("a", "b", "c"):
            schema = {"type": "object"}
            if stage == "b" and variant["schema"]:
                schema["description"] = "changed schema"
            messages = [
                {"role": "system", "content": "synthetic controller request"},
                {"role": "user", "content": json.dumps({"stage": stage, "dependencies": chain})},
            ]
            if stage == "b" and variant["request"]:
                messages[0]["content"] += " revised"
            result = json.loads(infer(messages, {"schema": schema}, stage))
            # Every reused response still runs this validation; it is not an accepted fact.
            if not result.get("valid") or (stage == "a" and variant.get("reject_reused")):
                raise DraftError("synthetic_validation_failed")
            chain.append(canonical_hash(result))
        return {"synthetic_controller_result": chain}

    monkeypatch.setattr(cloud.protocol, "extract", extract)
    return root, docs, variant


class Client:
    def __init__(self, fail=None):
        self.fail = fail
        self.calls = []

    def call(self, request, timeout):
        stage = json.loads(request["messages"][-1]["content"])["stage"]
        self.calls.append(stage)
        return response(
            {
                "model": cloud.MODEL,
                "choices": [
                    {
                        "finish_reason": "stop",
                        "message": {"content": json.dumps({"valid": stage != self.fail})},
                    }
                ],
                "usage": {"prompt_tokens": 100, "completion_tokens": 100},
            }
        )


def run(setup, name, client, **kwargs):
    root, docs, _ = setup
    output = root / "artifacts" / name
    result = cloud.run_cloud(root, [docs], output, client=client, **kwargs)
    return output, result


def test_resume_charges_only_new_requests_and_replays_without_network(setup):
    first, original = run(setup, "first", Client("c"))
    client = Client()
    second, result = run(setup, "second", client, previous_runs=[first], resume_from=first)
    assert client.calls == ["c"]
    assert result["responses_reused"] == 2
    assert result["requests_submitted"] == 1
    assert result["status"] == "accepted_candidate"
    assert not result["first_pass_accepted"]
    assert Decimal(result["observed_cost_cny"]) * 3 == Decimal(original["observed_cost_cny"])
    root, docs, _ = setup
    replay = cloud.replay_cloud(root, [docs], second, root / "artifacts/replay")
    assert replay["requests_submitted"] == 0
    assert replay["requests_replayed"] == 1
    assert replay["responses_reused"] == 2
    assert replay["outcome_and_artifact_equality_verified"]


@pytest.mark.parametrize("changed", ["schema", "request"])
def test_dependency_change_invalidates_entire_suffix(setup, changed):
    first, _ = run(setup, "first", Client("c"))
    setup[2][changed] = True
    client = Client()
    _, result = run(setup, "second", client, previous_runs=[first], resume_from=first)
    assert client.calls == ["b", "c"]
    assert result["responses_reused"] == 1
    assert result["resume_cache_stop_reason"] == "request_dependencies_changed"


def test_explicit_earlier_restart_and_failed_resume_remain_auditable(setup):
    first, _ = run(setup, "first", Client("c"))
    client = Client("c")
    second, result = run(
        setup, "second", client, previous_runs=[first], resume_from=first, restart_stage="b"
    )
    assert client.calls == ["b", "c"]
    assert result["status"] == "synthetic_validation_failed"
    third, result = run(setup, "third", Client(), previous_runs=[first, second], resume_from=second)
    assert result["responses_reused"] == 2
    root, docs, _ = setup
    assert cloud.replay_cloud(root, [docs], third, root / "artifacts/replay")[
        "outcome_and_artifact_equality_verified"
    ]


@pytest.mark.parametrize("changed", ["artifact", "source", "contract", "config", "lineage"])
def test_invalid_origin_rejected_before_new_request(setup, changed, monkeypatch):
    first, _ = run(setup, "first", Client("c"))
    root, docs, _ = setup
    if changed == "artifact":
        (first / "response-a.bin").write_text("changed")
    elif changed == "source":
        docs.write_text(docs.read_text() + "\n")
    elif changed == "contract":
        p = root / "ontology/private-lending-v0.3.json"
        value = json.loads(p.read_text())
        value["test_contract_revision"] = 2
        p.write_text(json.dumps(value))
    elif changed == "config":
        monkeypatch.setitem(cloud.CONFIG, "temperature", 0.3)
    client = Client()
    with pytest.raises(ValueError, match="Resume"):
        run(
            setup,
            "second",
            client,
            previous_runs=[] if changed == "lineage" else [first],
            resume_from=first,
        )
    assert client.calls == []


def test_reused_response_is_revalidated_before_any_new_request(setup):
    first, _ = run(setup, "first", Client("c"))
    setup[2]["reject_reused"] = True
    client = Client()
    _, result = run(setup, "second", client, previous_runs=[first], resume_from=first)
    assert result["responses_reused"] == 1
    assert result["status"] == "synthetic_validation_failed"
    assert client.calls == []
    assert result["observed_cost_cny"] == "0"


def test_successful_origin_and_unknown_restart_rejected(setup):
    first, _ = run(setup, "first", Client())
    with pytest.raises(ValueError, match="unsuccessful"):
        run(setup, "second", Client(), previous_runs=[first], resume_from=first)
    failed, _ = run(setup, "failed", Client("c"))
    with pytest.raises(ValueError, match="Unknown restart"):
        run(
            setup,
            "unknown",
            Client(),
            previous_runs=[failed],
            resume_from=failed,
            restart_stage="z",
        )


def test_resume_attempt_budget_cannot_reset_by_selecting_an_older_origin(setup):
    first, _ = run(setup, "first", Client("c"))
    prior = [first]
    for i in range(3):
        out, result = run(setup, f"resume-{i}", Client("c"), previous_runs=prior, resume_from=first)
        assert result["resume_attempt"] == i + 1
        prior.append(out)
    client = Client()
    with pytest.raises(ValueError, match="attempt budget exhausted"):
        run(setup, "too-many", client, previous_runs=prior, resume_from=first)
    assert client.calls == []


def test_nonfirst_document_is_selected_and_replayed_without_reordering_inputs(setup):
    root, docs, _ = setup
    values = json.loads(docs.read_text())
    second = json.loads(json.dumps(values[0]))
    second["source"]["record_pointer"] = "/1"
    second["document_id"] = second["dataset"] + ":" + canonical_hash(second["source"])
    values.append(second)
    docs.write_text(json.dumps(values))
    output, result = run(setup, "selected-second", Client(), document_id=second["document_id"])
    assert result["selected_document_id"] == second["document_id"]
    assert result["expected_documents"] == 2
    assert result["not_selected"] == 1
    selected = json.loads((output / "selected-documents.json").read_text())
    assert selected == [second]
    replay = cloud.replay_cloud(root, [docs], output, root / "artifacts/replay-second")
    assert replay["outcome_and_artifact_equality_verified"]
    assert replay["selected_document_id"] == second["document_id"]
