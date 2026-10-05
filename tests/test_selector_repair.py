"""Finite selectors tested with metadata, never invented legal cases or gold."""

import copy
import json
from pathlib import Path

import pytest
from test_anchored import PROVENANCE
from test_extraction import MODEL, SimulatedClient, response, workspace  # noqa: F401
from test_extraction import source as extra_source
from test_mention_repair import CONTRACT, fixture

from judicial_kg.extraction import contextual
from judicial_kg.extraction.anchored import GroundingError
from judicial_kg.extraction.draft import DraftError
from judicial_kg.extraction.run import run_extraction
from judicial_kg.extraction.selector_repair import (
    VERSION,
    apply_choices,
    build_plan,
    prepare_repair,
    replay_repair,
    run_repair,
)
from judicial_kg.validation import validate_record

ROOT = Path(__file__).resolve().parents[1]


def decision(item, action="locate", **updates):
    return {
        "item_id": item["item_id"],
        "action": action,
        "choice_id": item["choices"][0]["choice_id"] if action == "locate" else None,
        "local_text": None,
        "mention_kind": None,
        "reason": "metadata position decision",
        **updates,
    }


def example():
    doc, draft, _, _, _ = fixture()
    plan = build_plan(doc, draft, CONTRACT)
    choices = {"selector_choices": [decision(i) for i in plan["selector_items"]]}
    return doc, draft, plan, choices


def test_no_baseline_unicode_immutable_semantics_and_no_qa():
    doc, draft, plan, choices = example()
    original = copy.deepcopy(draft)
    assert plan["baseline_used"] is False
    assert "HIDDEN_QA" not in json.dumps(plan)
    assert all("duplicate_targets" not in i for i in plan["selector_items"])
    patched, counts = apply_choices(draft, plan, choices)
    assert counts == {"locate": 2}
    assert draft == original and patched["contexts"] == original["contexts"]
    assert patched["actors"] == original["actors"]
    record = contextual.materialize(json.dumps(patched), doc, CONTRACT, PROVENANCE)
    assert validate_record(record, doc, CONTRACT) == []
    assert record["mentions"][0]["evidence"]["end"] == 2


@pytest.mark.parametrize(
    "mutation",
    [
        lambda c: c["selector_choices"].pop(),
        lambda c: c["selector_choices"].append(c["selector_choices"][0]),
        lambda c: c["selector_choices"][0].update(choice_id="outside"),
        lambda c: c.update(actors=[]),
        lambda c: c["selector_choices"][0].update(actor_id="other"),
        lambda c: c["selector_choices"][0].update(action="duplicate"),
        lambda c: c["selector_choices"][0].update(action="uncertain"),
    ],
)
def test_invalid_or_incomplete_decisions_cannot_mutate_facts(mutation):
    _, draft, plan, choices = example()
    original = copy.deepcopy(draft)
    mutation(choices)
    with pytest.raises(DraftError):
        apply_choices(draft, plan, choices)
    assert draft == original


def test_uncertain_preserves_rejection_and_frozen_draft_binding():
    _, draft, plan, choices = example()
    choices["selector_choices"][0] = decision(plan["selector_items"][0], "uncertain")
    with pytest.raises(GroundingError):
        apply_choices(draft, plan, choices)
    draft["contexts"][0]["events"][0]["polarity"] = "affirmed"
    with pytest.raises(ValueError, match="frozen"):
        apply_choices(draft, plan, choices)


def value_example(in_scope=True):
    doc, draft, _, _ = example()
    # Metadata values occur twice in u0; u1 contains B and is not interchangeable.
    context = draft["contexts"][0]
    prop = {
        "id": "v",
        "predicate": "other",
        "polarity": "negated",
        "value": {
            "kind": "text",
            "source": {
                "unit_id": "u1",
                "text": "A🙂",
                "occurrence": None,
            },
        },
    }
    context["events"][0]["statements"] = [prop]
    context["evidence_units"] = ["u0"] if in_scope else ["u1"]
    plan = build_plan(doc, draft, CONTRACT)
    return doc, draft, plan


def test_event_value_is_confined_to_original_context_and_literal():
    doc, draft, plan = value_example()
    item = plan["selector_items"][-1]
    assert item["target_kind"] == "statement_value"
    assert len(item["choices"]) == 2
    assert {i["unit_id"] for i in item["choices"]} == {"u0"}
    choices = {"selector_choices": [decision(i) for i in plan["selector_items"]]}
    patched, _ = apply_choices(draft, plan, choices)
    assert patched["contexts"][0]["events"][0]["polarity"] == "negated"
    record = contextual.materialize(json.dumps(patched), doc, CONTRACT, PROVENANCE)
    assert validate_record(record, doc, CONTRACT) == []
    choices["selector_choices"][-1] = decision(
        item, "local_span", local_text="A🙂", mention_kind="name"
    )
    with pytest.raises(DraftError, match="local_span_invalid"):
        apply_choices(draft, plan, choices)


def test_global_value_match_does_not_expand_context_or_force_success():
    _, draft, plan = value_example(False)
    item = plan["selector_items"][-1]
    assert item["choices"] == [] and item["scope_units"] == ["u1"]
    choices = {
        "selector_choices": [decision(i) for i in plan["selector_items"][:-1]]
        + [decision(item, "uncertain")]
    }
    with pytest.raises(GroundingError):
        apply_choices(draft, plan, choices)


@pytest.mark.parametrize(
    "text,valid", [("B", True), ("A🙂", False), ("absent", False), ("", False)]
)
def test_local_span_only_unique_in_original_unit(text, valid):
    doc, draft, _, _ = example()
    draft["mentions"] = [draft["mentions"][1]]
    draft["mentions"][0]["evidence"] = {
        "unit_id": "u1",
        "text": "UNSUPPORTED",
        "occurrence": None,
    }
    plan = build_plan(doc, draft, CONTRACT)
    choices = {
        "selector_choices": [
            decision(
                plan["selector_items"][0], "local_span", local_text=text, mention_kind="description"
            )
        ]
    }
    if valid:
        patched, _ = apply_choices(draft, plan, choices)
        assert patched["mentions"][0]["evidence"]["text"] == text
    else:
        with pytest.raises(DraftError):
            apply_choices(draft, plan, choices)


@pytest.mark.parametrize("version", ["qwen-extraction-v6", "qwen-extraction-v7"])
def test_saved_run_explicit_selection_full_denominator_replay_and_tampering(workspace, version):  # noqa: F811
    root, docs, config = workspace
    for name in [
        "ontology/private-lending-v0.2.json",
        "schemas/extraction-v0.2.schema.json",
        "prompts/qwen-extraction-v6.txt",
        "prompts/qwen-extraction-v7.txt",
        "prompts/qwen-selector-repair-v1.txt",
    ]:
        (root / name).write_bytes((ROOT / name).read_bytes())
    doc, draft, _, choices = example()
    docs.write_text(json.dumps([doc, extra_source(2)]))
    cfg = json.loads(config.read_text())
    cfg.update(version=version, max_documents=1)
    config.write_text(json.dumps(cfg))
    saved_draft = copy.deepcopy(draft)
    if version.endswith("v7"):
        mentions = saved_draft.pop("mentions")
        for actor in saved_draft["actors"]:
            actor["mentions"] = [
                {k: v for k, v in m.items() if k != "actor_id"}
                for m in mentions
                if m["actor_id"] == actor["id"]
            ]

    class Client(SimulatedClient):
        def call(self, path, body=None, timeout=15):
            if path != "/v1/chat/completions":
                return super().call(path, body, timeout)
            self.paths.append(path)
            value = choices if "selector_items" in body["messages"][-1]["content"] else saved_draft
            return response(
                {
                    "model": MODEL,
                    "choices": [
                        {"finish_reason": "stop", "message": {"content": json.dumps(value)}}
                    ],
                }
            )

    run = root / "artifacts/original"
    run_extraction(root, [docs], config, run, Client())
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
    # Target becomes second in full repair manifest; no implicit first-N selection.
    repair_docs = root / "repair-docs.json"
    repair_docs.write_text(json.dumps([extra_source(2), doc]))
    cfg["version"] = VERSION
    config.write_text(json.dumps(cfg))
    prepared = prepare_repair(root, [repair_docs], spec)
    assert prepared.drafts[doc["document_id"]] == draft
    for wanted in (["missing"], [doc["document_id"], doc["document_id"]]):
        prepared.selection_ids = wanted
        client = Client()
        with pytest.raises(ValueError, match="unique and present"):
            run_extraction(root, [repair_docs], config, root / "artifacts/bad", client, prepared)
        assert client.paths == []
        assert not (root / "artifacts/bad").exists()
    prepared.selection_ids = [doc["document_id"], extra_source(2)["document_id"]]
    with pytest.raises(ValueError, match="exactly retain"):
        run_extraction(root, [repair_docs], config, root / "artifacts/limited", Client(), prepared)
    out = root / "artifacts/repaired"
    result = run_repair(root, [repair_docs], spec, config, out, Client())
    assert result["document_status_counts"] == {"accepted_candidate": 1, "not_selected": 1}
    replay = root / "artifacts/replay"
    report = replay_repair(root, [repair_docs], spec, out, replay)
    assert report["requests_submitted"] == 0
    assert (out / "accepted.json").read_bytes() == (replay / "accepted.json").read_bytes()
    saved = json.loads((run / "report.json").read_text())
    call = next(c for c in saved["response_metadata"] if c["path"] == "/v1/chat/completions")
    original = run / f"rpc-{call['rpc']:03}.response.bin"
    original.write_bytes(original.read_bytes() + b" ")
    with pytest.raises(ValueError, match="hash mismatch"):
        prepare_repair(root, [repair_docs], spec)
