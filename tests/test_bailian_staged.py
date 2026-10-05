"""Cloud budget, no retry and exact offline replay on synthetic metadata only."""

import json
from decimal import Decimal

import pytest
from test_actor_inventory import selection
from test_context_first import prepare
from test_extraction import response, workspace  # noqa: F401
from test_scoped import ROOT, fixtures

from judicial_kg.extraction import bailian_staged as cloud


@pytest.mark.parametrize("version", [16, 17, 18, 19, 20, 21, 22, 23, 24])
@pytest.mark.parametrize("failure", [None, "unresolved", "missing_usage", "http", "budget"])
def test_cloud_shared_protocol_and_cost_ledger(workspace, monkeypatch, failure, version):  # noqa: F811
    root, docs, _ = prepare(workspace)
    for name in [
        "ontology/private-lending-v0.3.json",
        "schemas/extraction-v0.3.schema.json",
        f"prompts/qwen-extraction-v{version}.txt",
    ]:
        (root / name).write_bytes((ROOT / name).read_bytes())
    if failure == "budget":
        monkeypatch.setitem(cloud.CONFIG, "budget_cny", "0.000001")

    class Client:
        count = 0

        def call(self, request, timeout):
            n = self.count
            self.count += 1
            if failure == "http":
                return cloud.Response(503, b"{}", 0.01)
            payload = json.loads(request["messages"][-1]["content"])
            if n == 0:
                value = {"forms": ["A🙂", "missing"] if failure == "unresolved" else ["A🙂"]}
            elif n == 1:
                value = selection(payload["catalog"])
                if failure == "unresolved":
                    value["decisions"].append(
                        {"proposal_id": "p1", "actor_id": None, "span_id": None}
                    )
            elif n == 2:
                schema = json.loads(
                    request["messages"][0]["content"].split("只输出JSON，遵守本阶段Schema：\n")[-1]
                )
                value = fixtures()
                value["actors"] = (
                    payload["actor_catalog"]
                    if version >= 18
                    else schema["properties"]["actors"]["const"]
                )
                for c in value["contexts"]:
                    c["attributed_actor_ids"] = ["a"]
                if version >= 18:
                    value.pop("actors")
            elif version == 21 and n == 6:
                catalog = payload["catalog"]
                value = {
                    "catalog_sha256": catalog["catalog_sha256"],
                    "selections": [
                        {
                            "proposal_id": "v0",
                            "span_id": catalog["proposal_ledger"][0]["span_ids"][0],
                        }
                    ],
                }
            elif version >= 20:
                phase = (n - 3 - int(version == 21 and n > 6)) % (4 if version >= 23 else 3)
                if phase == 0:
                    value = {"forms": ["A🙂"] if n == 3 else []}
                elif phase == 1:
                    catalog = payload["catalog"]
                    spans = (
                        catalog["proposal_ledger"][0]["span_ids"] if catalog["proposals"] else []
                    )
                    value = {
                        "catalog_sha256": catalog["catalog_sha256"],
                        "mentions": [{"span_id": spans[0], "actor_id": "a", "kind": "name"}]
                        if spans
                        else [],
                        "proposal_decisions": [
                            {"proposal_id": "p0", "action": "select", "span_ids": spans[:1]}
                        ]
                        if spans
                        else [],
                    }
                elif phase == 3:
                    value = {"statements": [], "event_statements": {}}
                else:
                    value = {"roles": [], "events": [], "statements": []}
                    if version in (21, 22) and n == 5:
                        value["statements"] = [
                            {
                                "id": "s",
                                "subject_id": "a",
                                "predicate": "other",
                                "value": {
                                    "kind": "text",
                                    "source": (
                                        {"span_id": payload["evidence_catalog"][0]["span_id"]}
                                        if version == 22
                                        else {"text": "A🙂"}
                                    ),
                                },
                                "polarity": "affirmed",
                            }
                        ]
            elif n in (3, 5):
                value = {"forms": []}
            else:
                value = {
                    "catalog_sha256": payload["catalog_sha256"],
                    "mentions": [],
                    "proposal_decisions": [],
                    "roles": [],
                    "events": [],
                    "statements": [],
                }
            result = {
                "model": cloud.MODEL,
                "choices": [{"finish_reason": "stop", "message": {"content": json.dumps(value)}}],
            }
            if failure != "missing_usage":
                result["usage"] = {"prompt_tokens": 100, "completion_tokens": 100}
            return response(result)

    client = Client()
    run = root / "artifacts/cloud"
    result = cloud.run_cloud(root, [docs], run, client=client, version=version)
    expected = {
        None: "accepted_candidate",
        "unresolved": "inventory_unresolved",
        "missing_usage": "usage_missing",
        "http": "transport_or_http_failed",
        "budget": "cost_budget_exceeded",
    }[failure]
    assert result["status"] == expected
    assert (
        result["requests_submitted"]
        == {
            None: 11 if version >= 23 else 10 if version == 21 else 9 if version >= 20 else 7,
            "unresolved": 2,
            "missing_usage": 1,
            "http": 1,
            "budget": 0,
        }[failure]
    )
    assert Decimal(result["budget_committed_cny"]) <= Decimal(cloud.CONFIG["budget_cny"])
    if failure in ("http", "missing_usage"):
        assert Decimal(result["budget_committed_cny"]) > Decimal(result["observed_cost_cny"])
    replay = cloud.replay_cloud(root, [docs], run, root / "artifacts/replay-cloud")
    assert replay["requests_submitted"] == 0
    assert replay["outcome_and_artifact_equality_verified"]
    (run / "accepted.json").write_text("[] changed")
    with pytest.raises(ValueError, match="artifact changed"):
        cloud.replay_cloud(root, [docs], run, root / "artifacts/replay-bad")


def test_prior_budget_is_charged_before_any_new_request(workspace, monkeypatch):  # noqa: F811
    root, docs, _ = prepare(workspace)
    for name in [
        "ontology/private-lending-v0.3.json",
        "schemas/extraction-v0.3.schema.json",
        "prompts/qwen-extraction-v17.txt",
    ]:
        (root / name).write_bytes((ROOT / name).read_bytes())
    prior = root / "artifacts/prior"
    prior.mkdir(parents=True)
    (prior / "report.json").write_text(
        json.dumps(
            {"completed": True, "config": {"model": cloud.MODEL}, "budget_committed_cny": "4.99"}
        )
    )

    class NeverCalled:
        def call(self, *args):
            raise AssertionError("Budget should stop all network traffic")

    result = cloud.run_cloud(
        root,
        [docs],
        root / "artifacts/guard",
        client=NeverCalled(),
        version=17,
        previous_runs=[prior],
    )
    assert result["status"] == "cost_budget_exceeded"
    assert result["requests_submitted"] == 0
    assert result["prior_committed_cny"] == "4.99"
    assert result["budget_committed_cny"] == "0"
    replay = cloud.replay_cloud(root, [docs], root / "artifacts/guard", root / "artifacts/replay")
    assert replay["outcome_and_artifact_equality_verified"]
