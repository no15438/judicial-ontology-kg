"""V14 live-run protocol plumbing on metadata; simulated output is not model evidence."""

import json

import pytest
from test_context_first import prepare
from test_extraction import MODEL, SimulatedClient, response, workspace  # noqa: F401
from test_scoped import ROOT, fixtures

from judicial_kg.extraction import span_first
from judicial_kg.extraction.replay import replay_completed
from judicial_kg.extraction.run import run_extraction


@pytest.mark.parametrize("failure", [None, "unmatched", "invalid_span"])
def test_v14_fresh_stages_and_zero_call_replay(workspace, failure):  # noqa: F811
    root, docs, config = prepare(workspace)
    for name in [
        "ontology/private-lending-v0.3.json",
        "schemas/extraction-v0.3.schema.json",
        span_first.PROMPT_FILE,
    ]:
        (root / name).write_bytes((ROOT / name).read_bytes())
    cfg = json.loads(config.read_text())
    cfg["version"] = "qwen-extraction-v14"
    config.write_text(json.dumps(cfg))

    class Client(SimulatedClient):
        completed = 0

        def call(self, path, body=None, timeout=15):
            if path != "/v1/chat/completions":
                return super().call(path, body, timeout)
            self.paths.append(path)
            if self.completed == 0:
                value = fixtures()
            elif self.completed % 2:
                value = {"forms": ["missing" if failure == "unmatched" else "A🙂"]}
            else:
                payload = json.loads(body["messages"][-1]["content"])
                catalog = payload["span_catalog"]
                spans = (
                    catalog["proposal_ledger"][0]["span_ids"] if catalog["proposal_ledger"] else []
                )
                value = {
                    "catalog_sha256": payload["catalog_sha256"],
                    "mentions": (
                        [
                            {
                                "span_id": "invalid" if failure else spans[0],
                                "actor_id": "a",
                                "kind": fixtures()["actors"][0]["mentions"][0]["kind"],
                            }
                        ]
                        if spans
                        else []
                    ),
                    "proposal_decisions": [
                        {
                            "proposal_id": "p0",
                            "action": "select" if spans else "uncertain",
                            "span_ids": spans[:1],
                        }
                    ],
                    "roles": [],
                    "events": [],
                    "statements": [],
                }
                # The child source has no A🙂; preserve its empty proposal stage.
                if self.completed == 4:
                    value["proposal_decisions"] = []
            if self.completed == 3:
                value = {"forms": []}
            self.completed += 1
            return response(
                {
                    "model": MODEL,
                    "choices": [
                        {"finish_reason": "stop", "message": {"content": json.dumps(value)}}
                    ],
                }
            )

    run = root / "artifacts/v14"
    result = run_extraction(root, [docs], config, run, Client())
    assert result["document_status_counts"] == {
        (
            "span_proposals_unresolved"
            if failure == "unmatched"
            else "draft_grounding_failed"
            if failure
            else "accepted_candidate"
        ): 1
    }
    assert result["requests_submitted"] == (3 if failure else 5)
    assert (run / "stage-000-body-000-span-catalog.json").exists()
    if failure != "invalid_span":
        compiled = json.loads((run / "stage-000-body-000-span-compiled.json").read_text())
        assert compiled["status"] == ("unresolved" if failure else "source_grounded_stage")
    replay = replay_completed(root, [docs], run, root / "artifacts/replay")
    assert replay["requests_submitted"] == 0
    assert replay["outcome_and_candidate_equality_verified"]
