"""Source-bound metadata tests; these are not legal annotations."""

import copy
import json
from pathlib import Path

import pytest
from test_attribution_repair import fixture
from test_extraction import MODEL, SimulatedClient, response, source, workspace  # noqa: F401
from test_selector_repair import CONTRACT, decision, value_example

from judicial_kg.extraction import local_review, selector_repair
from judicial_kg.extraction.anchored import GroundingError
from judicial_kg.extraction.draft import DraftError
from judicial_kg.extraction.run import run_extraction


def choices(plan):
    return {
        "context_choices": [
            {
                "context_id": x["context_id"],
                "action": "classify",
                "kind": "party_claim",
                "reason": "metadata context",
            }
            for x in plan["context_items"]
        ],
        "mention_choices": [
            {
                "mention_id": x["mention_id"],
                "action": "reject_unsupported",
                "local_text": None,
                "kind": None,
                "reason": "metadata absent literal",
            }
            for x in plan["mention_items"]
        ],
        "property_choices": [
            {
                "statement_id": x["statement_id"],
                "action": "reject_outside_context",
                "reason": "metadata out of scope",
            }
            for x in plan["property_items"]
        ],
        "actor_choices": [
            {
                "actor_id": x["actor_id"],
                "action": "reject_unreferenced",
                "reason": "metadata no references",
            }
            for x in plan["actor_items"]
        ],
    }


def example():
    doc, draft, _, _ = fixture()
    draft["actors"].append({"id": "orphan", "actor_type": "organization"})
    plan = local_review.build_plan(doc, draft, CONTRACT)
    return doc, draft, plan, choices(plan)


def test_rejections_retained_and_unrelated_facts_immutable():
    _, draft, plan, ds = example()
    old = copy.deepcopy(draft)
    patched, rejected = local_review.apply_choices(draft, plan, ds)
    assert draft == old
    assert len(rejected) == 2
    assert patched["contexts"][0]["events"] == old["contexts"][0]["events"]
    assert (
        patched["contexts"][0]["attributed_actor_ids"] == old["contexts"][0]["attributed_actor_ids"]
    )
    assert patched["actors"] == old["actors"][:-1]
    assert patched["mentions"] == old["mentions"][:-1]
    assert all(r["status"] == "explicit_model_rejection_not_gold" for r in rejected)


@pytest.mark.parametrize(
    "text,kind,valid",
    [
        ("B", "description", True),
        ("A🙂", "name", False),
        ("", "pronoun", False),
        ("B", None, False),
    ],
)
def test_local_span_cannot_move_elsewhere_or_change_actor(text, kind, valid):
    _, draft, plan, ds = example()
    ds["mention_choices"][0].update(action="local_span", local_text=text, kind=kind)
    if valid:
        patched, _ = local_review.apply_choices(draft, plan, ds)
        assert patched["mentions"][-1]["actor_id"] == draft["mentions"][-1]["actor_id"]
        assert patched["mentions"][-1]["evidence"]["unit_id"] == "u1"
    else:
        with pytest.raises(DraftError):
            local_review.apply_choices(draft, plan, ds)


@pytest.mark.parametrize(
    "mutation",
    [
        lambda x: x["mention_choices"].clear(),
        lambda x: x["mention_choices"].append(x["mention_choices"][0]),
        lambda x: x["mention_choices"][0].update(actor_id="other"),
        lambda x: x["mention_choices"][0].update(local_text="B"),
        lambda x: x["context_choices"][0].update(kind="court_finding"),
        lambda x: x["actor_choices"][0].update(actor_id="a"),
    ],
)
def test_invalid_reviews_do_not_pass(mutation):
    _, draft, plan, ds = example()
    mutation(ds)
    with pytest.raises(DraftError):
        local_review.apply_choices(draft, plan, ds)


def test_uncertainty_keeps_entire_candidate_rejected_and_source_unchanged():
    _, draft, plan, ds = example()
    before = copy.deepcopy(draft)
    ds["actor_choices"][0]["action"] = "uncertain"
    with pytest.raises(GroundingError):
        local_review.apply_choices(draft, plan, ds)
    assert draft == before
    draft["contexts"][0]["events"][0]["polarity"] = "affirmed"
    with pytest.raises(ValueError, match="frozen"):
        local_review.apply_choices(draft, plan, ds)


def test_referenced_actors_never_in_rejection_catalog():
    _, draft, _, _ = example()
    draft["contexts"][0]["attributed_actor_ids"].append("orphan")
    plan = local_review.build_plan(example()[0], draft, CONTRACT)
    assert plan["actor_items"] == []


def test_property_rejection_confined_to_unsupported_original_statement():
    doc, draft, selectors = value_example(False)
    ds = {
        "selector_choices": [
            decision(i, "uncertain" if i["target_kind"] == "statement_value" else "locate")
            for i in selectors["selector_items"]
        ]
    }
    draft, _, _ = selector_repair.stage_choices(draft, selectors, ds)
    plan = local_review.build_plan(doc, draft, CONTRACT)
    assert len(plan["property_items"]) == 1
    ds = choices(plan)
    # The fixture also has selector-only errors which the previous stage normally resolves.
    ds["mention_choices"] = []
    assert plan["mention_items"] == []
    patched, rejected = local_review.apply_choices(draft, plan, ds)
    assert patched["contexts"][0]["events"][0]["statements"] == []
    assert rejected[0]["original_assertion"]["id"] == "v"
    assert patched["contexts"][0]["events"][0]["polarity"] == "negated"


def test_saved_stage_source_replay_and_rejected_ledger_conservation(workspace):  # noqa: F811
    root, docs, config = workspace
    project = Path(__file__).resolve().parents[1]
    for name in [
        "ontology/private-lending-v0.2.json",
        "schemas/extraction-v0.2.schema.json",
        "prompts/qwen-extraction-v6.txt",
        "prompts/qwen-selector-repair-v1.txt",
        "prompts/qwen-local-review-v1.txt",
    ]:
        (root / name).write_bytes((project / name).read_bytes())
    doc, draft, _, _ = fixture()
    plan = selector_repair.build_plan(doc, draft, CONTRACT)
    parent_choices = {
        "selector_choices": [decision(i, "uncertain") for i in plan["selector_items"]]
    }
    target = local_review.build_plan(doc, draft, CONTRACT)
    ds = choices(target)
    docs.write_text(json.dumps([doc, source(2)]))
    cfg = json.loads(config.read_text())
    cfg.update(version="qwen-extraction-v6", max_documents=1)
    config.write_text(json.dumps(cfg))

    class Client(SimulatedClient):
        def call(self, path, body=None, timeout=15):
            if path != "/v1/chat/completions":
                return super().call(path, body, timeout)
            self.paths.append(path)
            msg = body["messages"][-1]["content"]
            value = (
                ds
                if "property_items" in msg
                else parent_choices
                if "selector_items" in msg
                else draft
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
    parent = root / "artifacts/parent"
    selector_repair.run_repair(root, [docs], spec, config, parent, Client())
    cfg["version"] = local_review.VERSION
    config.write_text(json.dumps(cfg))
    out = root / "artifacts/review"
    report = local_review.run_repair(root, [docs], spec, parent, config, out, Client())
    assert report["document_status_counts"] == {"accepted_candidate": 1, "not_selected": 1}
    replay = root / "artifacts/replay"
    proof = local_review.replay_repair(root, [docs], spec, parent, out, replay)
    assert proof["requests_submitted"] == 0
    assert proof["accepted_candidates_match_saved"] and proof["outcomes_match_saved"]
    for f in out.glob("rejected-assertions-*.json"):
        assert f.read_bytes() == (replay / f.name).read_bytes()
    saved = json.loads((parent / "report.json").read_text())
    call = next(c for c in saved["response_metadata"] if c["path"] == "/v1/chat/completions")
    response_file = parent / f"rpc-{call['rpc']:03}.response.bin"
    response_file.write_bytes(response_file.read_bytes() + b" ")
    with pytest.raises(ValueError, match="hash mismatch"):
        local_review.prepare_repair(root, [docs], spec, parent)
