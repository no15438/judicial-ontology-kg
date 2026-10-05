"""Explicit model review has bounded edits and preserves rejected assertions."""

import copy
import json

import pytest
from test_anchored import PROVENANCE
from test_selector_repair import CONTRACT, example

from judicial_kg.extraction import contextual
from judicial_kg.extraction.anchored import GroundingError
from judicial_kg.extraction.attribution_repair import apply_choices, build_plan
from judicial_kg.extraction.draft import DraftError
from judicial_kg.validation import validate_record


def fixture():
    doc, draft, _, _ = example()
    draft["mentions"][0]["evidence"]["occurrence"] = 0
    draft["mentions"][1]["evidence"]["unit_id"] = "u1"
    known = copy.deepcopy(draft["mentions"][0])
    known["id"] = "known_a2"
    known["evidence"]["occurrence"] = 1
    draft["mentions"].append(known)
    unsupported = copy.deepcopy(known)
    unsupported.update(id="unsupported", kind="name")
    unsupported["evidence"] = {"unit_id": "u1", "text": "A🙂", "occurrence": None}
    draft["mentions"].append(unsupported)
    draft["contexts"][0].update(kind="court_report", attributed_actor_ids=["a"])
    plan = build_plan(doc, draft, CONTRACT)
    decisions = {
        "context_choices": [
            {
                "context_id": "c",
                "action": "classify",
                "kind": "party_claim",
                "reason": "metadata source",
            }
        ],
        "mention_choices": [
            {
                "mention_id": "unsupported",
                "action": "reject_unsupported",
                "source_peer_mention_id": "mb",
                "reason": "metadata peer",
            }
        ],
    }
    return doc, draft, plan, decisions


def test_review_preserves_facts_and_records_explicit_unsupported_assertion():
    doc, draft, plan, choices = fixture()
    before = copy.deepcopy(draft)
    assert len(plan["context_items"]) == len(plan["mention_items"]) == 1
    assert "HIDDEN_QA" not in json.dumps(plan)
    patched, rejected = apply_choices(draft, plan, choices)
    assert draft == before
    assert rejected[0]["original_assertion"] == draft["mentions"][-1]
    assert rejected[0]["status"] == "explicit_model_rejection_not_gold"
    assert patched["actors"] == before["actors"]
    assert patched["contexts"][0]["events"] == before["contexts"][0]["events"]
    assert patched["contexts"][0]["attributed_actor_ids"] == ["a"]
    assert len(patched["mentions"]) == len(draft["mentions"]) - 1
    record = contextual.materialize(json.dumps(patched), doc, CONTRACT, PROVENANCE)
    assert validate_record(record, doc, CONTRACT) == []


@pytest.mark.parametrize(
    "mutation",
    [
        lambda c: c["context_choices"][0].update(kind="court_finding"),
        lambda c: c["context_choices"][0].update(attributed_actor_ids=[]),
        lambda c: c["mention_choices"][0].update(source_peer_mention_id="unsupported"),
        lambda c: c["mention_choices"][0].update(actor_id="b"),
        lambda c: c["mention_choices"].pop(),
        lambda c: c["mention_choices"].append(c["mention_choices"][0]),
        lambda c: c["context_choices"][0].update(action="uncertain"),
        lambda c: c["mention_choices"][0].update(action="uncertain"),
    ],
)
def test_illegal_semantic_changes_and_hidden_deletion_are_rejected(mutation):
    _, draft, plan, choices = fixture()
    mutation(choices)
    with pytest.raises(DraftError):
        apply_choices(draft, plan, choices)


def test_uncertain_does_not_delete_or_mutate_and_source_is_frozen():
    _, draft, plan, choices = fixture()
    choices["mention_choices"][0].update(action="uncertain", source_peer_mention_id=None)
    before = copy.deepcopy(draft)
    with pytest.raises(GroundingError):
        apply_choices(draft, plan, choices)
    assert draft == before
    draft["contexts"][0]["events"][0]["polarity"] = "affirmed"
    with pytest.raises(ValueError, match="frozen"):
        apply_choices(draft, plan, choices)


@pytest.mark.parametrize("change", ["pronoun", "unoccupied", "no_peer", "mixed_court"])
def test_plan_excludes_unsupported_or_ambiguous_review_scope(change):
    doc, draft, _, _ = fixture()
    if change == "pronoun":
        draft["mentions"][-1]["kind"] = "pronoun"
    elif change == "unoccupied":
        draft["mentions"].pop(2)
    elif change == "no_peer":
        draft["mentions"].pop(1)
    else:
        draft["actors"][1]["actor_type"] = "court"
        draft["contexts"][0]["attributed_actor_ids"] = ["a", "b"]
    plan = build_plan(doc, draft, CONTRACT)
    assert not plan["context_items"] if change == "mixed_court" else not plan["mention_items"]


def test_parent_verified_full_denominator_offline_replay_and_tampering(tmp_path, monkeypatch):
    import subprocess
    from pathlib import Path

    from test_extraction import MODEL, SimulatedClient, response, source

    from judicial_kg.extraction import attribution_repair, selector_repair
    from judicial_kg.extraction.run import run_extraction

    monkeypatch.setattr(
        subprocess, "check_output", lambda *a, **kw: "test-commit" if kw.get("text") else b""
    )
    root = tmp_path
    (root / "uv.lock").write_bytes((Path(__file__).resolve().parents[1] / "uv.lock").read_bytes())
    project = Path(__file__).resolve().parents[1]
    for name in [
        "ontology/private-lending-v0.2.json",
        "schemas/extraction-v0.2.schema.json",
        "prompts/qwen-extraction-v6.txt",
        "prompts/qwen-selector-repair-v1.txt",
        "prompts/qwen-attribution-repair-v1.txt",
    ]:
        target = root / name
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes((project / name).read_bytes())
    doc, draft, _, choices = example()
    draft["contexts"][0].update(kind="court_report", attributed_actor_ids=["a"])
    docs = root / "documents.json"
    docs.write_text(json.dumps([doc, source(2)]))
    cfg = json.loads((project / "configs/qwen-attribution-repair-v1.json").read_text())
    cfg.update(version="qwen-extraction-v6", max_documents=1)
    config = root / "config.json"
    config.write_text(json.dumps(cfg))
    decisions = {
        "context_choices": [
            {"context_id": "c", "action": "classify", "kind": "party_claim", "reason": "metadata"}
        ],
        "mention_choices": [],
    }

    class Client(SimulatedClient):
        def call(self, path, body=None, timeout=15):
            if path != "/v1/chat/completions":
                return super().call(path, body, timeout)
            self.paths.append(path)
            content = body["messages"][-1]["content"]
            value = (
                decisions
                if "context_items" in content
                else (choices if "selector_items" in content else draft)
            )
            return response(
                {
                    "model": MODEL,
                    "choices": [
                        {"finish_reason": "stop", "message": {"content": json.dumps(value)}}
                    ],
                }
            )

    original = root / "artifacts/original"
    run_extraction(root, [docs], config, original, Client())
    spec = root / "spec.json"
    spec.write_text(
        json.dumps(
            [
                {
                    "source_run": "artifacts/original",
                    "documents": [docs.name],
                    "document_ids": [doc["document_id"]],
                }
            ]
        )
    )
    cfg["version"] = selector_repair.VERSION
    config.write_text(json.dumps(cfg))
    parent = root / "artifacts/selector"
    selector_repair.run_repair(root, [docs], spec, config, parent, Client())
    cfg["version"] = attribution_repair.VERSION
    config.write_text(json.dumps(cfg))
    output = root / "artifacts/review"
    report = attribution_repair.run_repair(root, [docs], spec, parent, config, output, Client())
    assert report["document_status_counts"] == {"accepted_candidate": 1, "not_selected": 1}
    replay = root / "artifacts/replay"
    proof = attribution_repair.replay_repair(root, [docs], spec, parent, output, replay)
    assert proof["requests_submitted"] == 0
    assert proof["accepted_candidates_match_saved"] and proof["outcomes_match_saved"]
    assert (output / "accepted.json").read_bytes() == (replay / "accepted.json").read_bytes()
    for ledger in output.glob("rejected-assertions-*.json"):
        assert ledger.read_bytes() == (replay / ledger.name).read_bytes()
    saved = json.loads((parent / "report.json").read_text())
    call = next(c for c in saved["response_metadata"] if c["path"] == "/v1/chat/completions")
    raw = parent / f"rpc-{call['rpc']:03}.response.bin"
    raw.write_bytes(raw.read_bytes() + b" ")
    with pytest.raises(ValueError, match="hash mismatch"):
        attribution_repair.prepare_repair(root, [docs], spec, parent)
