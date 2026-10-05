"""Cloud budget, no retry and exact offline replay on synthetic metadata only."""

import json
from decimal import Decimal

import pytest
from test_actor_inventory import selection
from test_context_first import prepare
from test_extraction import response, workspace  # noqa: F401
from test_scoped import ROOT, fixtures

from judicial_kg.extraction import bailian_staged as cloud


@pytest.mark.parametrize("failure", [None, "unresolved", "missing_usage", "http", "budget"])
def test_cloud_shared_protocol_and_cost_ledger(workspace, monkeypatch, failure):  # noqa: F811
    root, docs, _ = prepare(workspace)
    for name in [
        "ontology/private-lending-v0.3.json",
        "schemas/extraction-v0.3.schema.json",
        cloud.protocol.PROMPT_FILE,
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
                value["actors"] = schema["properties"]["actors"]["const"]
                for c in value["contexts"]:
                    c["attributed_actor_ids"] = ["a"]
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
    result = cloud.run_cloud(root, [docs], run, client=client)
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
        == {None: 7, "unresolved": 2, "missing_usage": 1, "http": 1, "budget": 0}[failure]
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
