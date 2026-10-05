"""Exact mention echoes and literal review cues on metadata, not legal gold."""

import copy
import json

import pytest
from test_anchored import PROVENANCE, source
from test_context_first import prepare
from test_extraction import MODEL, SimulatedClient, response, workspace  # noqa: F401
from test_scoped import CONTRACT, ROOT, fixtures

from judicial_kg.extraction import scoped
from judicial_kg.extraction import scoped_incremental as protocol
from judicial_kg.extraction.draft import DraftError
from judicial_kg.extraction.replay import replay_completed
from judicial_kg.extraction.run import run_extraction
from judicial_kg.sources.records import canonical_hash


def echo_body(header):
    mention = copy.deepcopy(header["actors"][0]["mentions"][0])
    mention.update(id="echo", actor_id=header["actors"][0]["id"])
    return {"mentions": [mention], "roles": [], "events": [], "statements": []}


def test_only_exact_grounded_echo_reuses_anchor_and_raw_receipt_survives():
    h = fixtures()
    b = echo_body(h)
    before = canonical_hash([h, b])
    saved = {}
    merged = protocol.merge_body(h, 0, b, lambda k, v: saved.update({k: v}))
    assert merged["actors"] == h["actors"]
    assert canonical_hash([h, b]) == before
    record = scoped.compile_checked(merged, source(), CONTRACT, PROVENANCE)
    assert len(record["mentions"]) == 2
    alias = saved["body-000-mention-reuse"]["aliases"][0]
    assert alias["canonical_mention_id"] == h["actors"][0]["mentions"][0]["id"]
    assert alias["model_mention_id"] == "v13_b0_echo"
    assert alias["selector"] == b["mentions"][0]["evidence"]
    # V12 must retain its old rejection, rather than retroactively accepting it.
    old = scoped.identity_first.merge_body(h, 0, b, namespace="v12")
    with pytest.raises(DraftError) as raised:
        scoped.compile_checked(old, source(), CONTRACT, PROVENANCE)
    assert "duplicate_mention_evidence" in {i["code"] for i in raised.value.issues}


@pytest.mark.parametrize("changed", ["actor", "kind", "text", "unit", "occurrence"])
def test_changed_actor_class_text_or_location_is_never_collapsed(changed):
    h = fixtures()
    b = echo_body(h)
    m = b["mentions"][0]
    if changed == "actor":
        m["actor_id"] = h["actors"][1]["id"]
    elif changed == "kind":
        m["kind"] = "name" if m["kind"] != "name" else "description"
    else:
        m["evidence"][
            ["unit_id", "text", "occurrence"][["unit", "text", "occurrence"].index(changed)]
        ] = {
            "unit": "u3",
            "text": "missing",
            "occurrence": 1,
        }[changed]
    saved = {}
    result = protocol.merge_body(h, 0, b, lambda k, v: saved.update({k: v}))
    assert saved["body-000-mention-reuse"]["aliases"] == []
    assert sum(len(a["mentions"]) for a in result["actors"]) == 3


def test_two_unvalidated_new_mentions_and_local_id_collision_still_reject():
    h = fixtures()
    b = echo_body(h)
    b["mentions"][0]["evidence"] = {"unit_id": "u0", "text": "🙂", "occurrence": 0}
    b["mentions"].append({**copy.deepcopy(b["mentions"][0]), "id": "echo2"})
    saved = {}
    merged = protocol.merge_body(h, 0, b, lambda k, v: saved.update({k: v}))
    assert saved["body-000-mention-reuse"]["aliases"] == []
    with pytest.raises(DraftError):
        scoped.compile_checked(merged, source(), CONTRACT, PROVENANCE)
    b["mentions"][1]["id"] = b["mentions"][0]["id"]
    with pytest.raises(DraftError):
        protocol.merge_body(h, 0, b, lambda *_: None)


def test_review_cues_use_direct_text_excluding_enclosing_quote_and_do_not_relabel():
    text = "字段A。原告主张字段B。"
    doc = {"fields": [{"name": "alpha", "text": text}]}
    c = {
        "id": "c",
        "kind": "court_finding",
        "evidence": [{"field": "alpha", "start": 0, "end": len(text)}],
        "assertion_evidence": [{"field": "alpha", "start": 0, "end": 4}],
    }
    record = {"contexts": [c]}
    saved = {}
    before = canonical_hash(record)
    protocol.header_diagnostics(record, doc, lambda k, v: saved.update({k: v}))
    assert saved["header-diagnostics"]["hints"] == []
    assert canonical_hash(record) == before
    c["assertion_evidence"][0]["end"] = len(text)
    before = canonical_hash(record)
    protocol.header_diagnostics(record, doc, lambda k, v: saved.update({k: v}))
    assert (
        saved["header-diagnostics"]["hints"][0]["code"] == "party_reporting_cue_in_direct_finding"
    )
    assert not saved["header-diagnostics"]["acceptance_modified"]
    assert canonical_hash(record) == before


@pytest.mark.parametrize("failure", [False, True])
def test_v13_runner_replay_preserves_aliases_failures_and_all_raw_stages(workspace, failure):  # noqa: F811
    root, docs, config = prepare(workspace)
    for name in [
        "ontology/private-lending-v0.3.json",
        "schemas/extraction-v0.3.schema.json",
        protocol.PROMPT_FILE,
    ]:
        (root / name).write_bytes((ROOT / name).read_bytes())
    cfg = json.loads(config.read_text())
    cfg["version"] = "qwen-extraction-v13"
    config.write_text(json.dumps(cfg))

    class Client(SimulatedClient):
        completed = 0

        def call(self, path, body=None, timeout=15):
            if path != "/v1/chat/completions":
                return super().call(path, body, timeout)
            self.paths.append(path)
            h = fixtures()
            value = (
                h
                if not self.completed
                else echo_body(h)
                if self.completed == 1
                else {"mentions": [], "roles": [], "events": [], "statements": []}
            )
            if failure and self.completed == 1:
                value["mentions"][0]["evidence"]["text"] = "missing"
            self.completed += 1
            return response(
                {
                    "model": MODEL,
                    "choices": [
                        {"finish_reason": "stop", "message": {"content": json.dumps(value)}}
                    ],
                }
            )

    run = root / "artifacts/v13"
    result = run_extraction(root, [docs], config, run, Client())
    assert result["document_status_counts"] == {
        "draft_grounding_failed" if failure else "accepted_candidate": 1
    }
    assert result["requests_submitted"] == (2 if failure else 3)
    assert (run / "stage-000-body-000-draft.json").exists()
    assert (run / "stage-000-body-000-mention-reuse.json").exists()
    replay = replay_completed(root, [docs], run, root / "artifacts/v13-replay")
    assert replay["requests_submitted"] == 0
    assert replay["outcome_and_candidate_equality_verified"]
