"""Actor-local mention structure checked on metadata, not fabricated legal documents."""

import copy
import json
from pathlib import Path

import pytest
from test_anchored import PROVENANCE, source
from test_contextual import joint_draft
from test_extraction import MODEL, SimulatedClient, response, workspace  # noqa: F401

from judicial_kg.contracts import Contract
from judicial_kg.extraction import actor_grounded, contextual
from judicial_kg.extraction.draft import DraftError
from judicial_kg.extraction.replay import replay_completed
from judicial_kg.extraction.run import run_extraction

ROOT = Path(__file__).resolve().parents[1]
CONTRACT = Contract.load(ROOT, "0.2.0")


def nested():
    old = joint_draft()
    new = copy.deepcopy(old)
    del new["mentions"]
    for actor in new["actors"]:
        actor["mentions"] = [
            {k: v for k, v in m.items() if k != "actor_id"}
            for m in old["mentions"]
            if m["actor_id"] == actor["id"]
        ]
    return old, new


def test_actor_local_mentions_compile_to_the_same_final_contract():
    old, new = nested()
    assert actor_grounded.unpack_draft(json.dumps(new), CONTRACT)["actors"] == old["actors"]
    result = actor_grounded.materialize(json.dumps(new), source(), CONTRACT, PROVENANCE)
    expected = contextual.materialize(json.dumps(old), source(), CONTRACT, PROVENANCE)
    # Nesting changes mention order only to actor order; coordinates and identity stay exact.
    assert {m["id"]: m for m in result["mentions"]} == {m["id"]: m for m in expected["mentions"]}
    assert result["contexts"] == expected["contexts"] and result["events"] == expected["events"]


@pytest.mark.parametrize(
    "change",
    [
        lambda n: n["actors"][0].update(mentions=[]),
        lambda n: n["actors"][0]["mentions"][0].update(actor_id="b"),
        lambda n: n.update(mentions=[]),
        lambda n: n["actors"].append({"id": "placeholder", "actor_type": "unknown"}),
    ],
)
def test_actor_shells_and_conflicting_identity_are_rejected(change):
    _, new = nested()
    change(new)
    with pytest.raises(DraftError):
        actor_grounded.materialize(json.dumps(new), source(), CONTRACT, PROVENANCE)


@pytest.mark.parametrize(
    "version", ["qwen-extraction-v7", "qwen-extraction-v8", "qwen-extraction-v9"]
)
def test_actor_grounded_run_and_saved_replay(workspace, version):  # noqa: F811
    root, docs, config_path = workspace
    for name in (
        "ontology/private-lending-v0.2.json",
        "schemas/extraction-v0.2.schema.json",
        "prompts/qwen-extraction-v7.txt",
        "prompts/qwen-extraction-v8.txt",
        "prompts/qwen-extraction-v9.txt",
    ):
        (root / name).write_bytes((ROOT / name).read_bytes())
    docs.write_text(json.dumps([source()]))
    c = json.loads(config_path.read_text())
    c["version"] = version
    config_path.write_text(json.dumps(c))

    class Client(SimulatedClient):
        def call(self, path, body=None, timeout=15):
            if path == "/v1/chat/completions":
                if version.endswith("v9"):
                    assert "schema" in body["response_format"]
                    assert body["response_format"]["schema"]["$defs"]["selector"]["properties"][
                        "unit_id"
                    ]["enum"]
                else:
                    assert body["response_format"] == {"type": "json_object"}
                self.paths.append(path)
                return response(
                    {
                        "model": MODEL,
                        "choices": [
                            {
                                "finish_reason": "stop",
                                "message": {"content": json.dumps(nested()[1])},
                            }
                        ],
                    }
                )
            return super().call(path, body, timeout)

    run = root / "artifacts/v7"
    r = run_extraction(root, [docs], config_path, run, Client())
    assert r["document_status_counts"] == {"accepted_candidate": 1}
    replay = replay_completed(root, [docs], run, root / "artifacts/v7-replay")
    assert replay["requests_submitted"] == 0
    assert replay["document_status_counts"] == {"accepted_candidate": 1}
