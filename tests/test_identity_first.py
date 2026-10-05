"""Representative identity anchors with source-local mention completion; metadata only."""

import copy
import json

import pytest
from test_anchored import PROVENANCE, source
from test_context_first import CONTRACT, ROOT, fixtures, prepare
from test_extraction import MODEL, SimulatedClient, response, workspace  # noqa: F401

from judicial_kg.extraction import identity_first as protocol
from judicial_kg.extraction.draft import DraftError
from judicial_kg.extraction.replay import replay_completed
from judicial_kg.extraction.run import run_extraction


def identity_fixtures():
    header, body = fixtures()
    for a in header["actors"]:
        a["mentions"] = a["mentions"][:1]
    body["mentions"] = [
        {
            "id": "mx",
            "actor_id": "a",
            "kind": "description",
            "evidence": {"unit_id": "u1", "text": "42", "occurrence": 0},
        }
    ]
    return header, body


def test_anchor_schema_and_local_mentions_have_independent_source_constraints():
    header, body = identity_fixtures()
    protocol.base.check_schema(header, protocol.draft_schema(CONTRACT))
    doubled = copy.deepcopy(header)
    doubled["actors"][0]["mentions"].append(copy.deepcopy(doubled["actors"][0]["mentions"][0]))
    with pytest.raises(DraftError):
        protocol.base.check_schema(doubled, protocol.draft_schema(CONTRACT))
    schema = protocol.body_schema(header, header["contexts"][0], CONTRACT)
    protocol.base.check_schema(body, schema)
    for key, value in [("actor_id", "foreign"), ("id", "")]:
        changed = copy.deepcopy(body)
        changed["mentions"][0][key] = value
        with pytest.raises(DraftError):
            protocol.base.check_schema(changed, schema)
    body["mentions"][0]["evidence"]["unit_id"] = "u3"
    with pytest.raises(DraftError):
        protocol.base.check_schema(body, schema)


def test_additional_mentions_keep_anchor_and_identity_unchanged_and_grounded():
    header, body = identity_fixtures()
    result = protocol.merge_body(header, 0, body)
    for old, actor in zip(header["actors"], result["actors"], strict=True):
        assert old["id"] == actor["id"] and old["actor_type"] == actor["actor_type"]
        assert actor["mentions"][:1] == old["mentions"]
    compiled = protocol.base.compile_checked(result, source(), CONTRACT, PROVENANCE)
    assert len(compiled["mentions"]) == 3
    assert compiled["mentions"][-1]["evidence"]["quote"] == "B"
    assert any(m["evidence"]["quote"] == "42" for m in compiled["mentions"])
    assert result["contexts"][0]["events"][0]["id"] == "v11_b0_e"
    body["mentions"][0]["id"] = body["events"][0]["id"]
    with pytest.raises(DraftError):
        protocol.merge_body(header, 0, body)


@pytest.mark.parametrize("failure", [False, True])
def test_v11_real_runner_and_offline_replay_preserve_failure_boundary(workspace, failure):  # noqa: F811
    root, docs, config_path = prepare(workspace)
    (root / protocol.PROMPT_FILE).write_bytes((ROOT / protocol.PROMPT_FILE).read_bytes())
    cfg = json.loads(config_path.read_text())
    cfg["version"] = "qwen-extraction-v11"
    config_path.write_text(json.dumps(cfg))

    class Client(SimulatedClient):
        completed = 0

        def call(self, path, body=None, timeout=15):
            if path != "/v1/chat/completions":
                return super().call(path, body, timeout)
            self.paths.append(path)
            header, children = identity_fixtures()
            if self.completed:
                content = children
                if failure:
                    content["mentions"][0]["evidence"]["text"] = "missing"
            else:
                content = header
            self.completed += 1
            return response(
                {
                    "model": MODEL,
                    "choices": [
                        {
                            "finish_reason": "stop",
                            "message": {"content": json.dumps(content)},
                        }
                    ],
                }
            )

    run = root / "artifacts/identity"
    report = run_extraction(root, [docs], config_path, run, Client())
    assert report["requests_submitted"] == 2
    assert report["document_status_counts"] == {
        "draft_grounding_failed" if failure else "accepted_candidate": 1,
    }
    replay = replay_completed(root, [docs], run, root / "artifacts/identity-replay")
    assert replay["requests_submitted"] == 0
    assert replay["outcome_and_candidate_equality_verified"]


def test_failed_local_mention_leaves_later_context_unattempted_without_partial_acceptance():
    header, body = identity_fixtures()
    other = copy.deepcopy(header["contexts"][0])
    other.update(id="c2", evidence_units=["u3"])
    header["contexts"].append(other)
    body["mentions"][0]["evidence"]["text"] = "missing"
    calls, saved = [], {}

    def infer(messages, fmt, key):
        calls.append(key)
        return json.dumps(header if key == "header" else body)

    def save(key, value):
        saved[key] = copy.deepcopy(value)

    with pytest.raises(DraftError):
        protocol.extract(source(), "prompt", CONTRACT, PROVENANCE, infer, save)
    assert calls == ["header", "body-000"]
    assert [c["status"] for c in saved["stage-plan"]["contexts"]] == ["failed", "not_attempted"]
    assert not saved["stage-plan"]["complete"]
    assert "combined-draft" not in saved
