"""Repair choice boundaries tested using metadata strings, never synthetic cases."""

import copy
import json
from pathlib import Path

import pytest
from test_anchored import PROVENANCE
from test_extraction import MODEL, SimulatedClient, response, workspace  # noqa: F401

from judicial_kg.contracts import Contract
from judicial_kg.extraction import contextual, typed
from judicial_kg.extraction.anchored import GroundingError
from judicial_kg.extraction.draft import DraftError
from judicial_kg.extraction.mention_repair import apply_choices, build_plan, run_repair
from judicial_kg.extraction.run import run_extraction
from judicial_kg.sources.records import document, sha256
from judicial_kg.validation import validate_record

ROOT = Path(__file__).resolve().parents[1]
CONTRACT = Contract.load(ROOT, "0.2.0")


def fixture():
    doc = document(
        "metadata",
        {"record_pointer": "/0"},
        {"field": ("/0/f", "A🙂 A🙂，B。C。")},
        family_id="family:0",
        allocation="development_exposed",
        reference_labels=[{"secret": "HIDDEN_QA"}],
    )
    baseline_draft = {
        "actors": [{"id": x, "actor_type": "unknown"} for x in "abc"],
        "mentions": [
            {
                "id": "m" + x,
                "actor_id": x,
                "kind": "description",
                "evidence": {"unit_id": "u" + str(i), "text": t, "occurrence": 0},
            }
            for i, (x, t) in enumerate(zip("abc", ["A🙂", "B", "C"], strict=True))
        ],
        "contexts": [],
    }
    baseline = typed.materialize(json.dumps(baseline_draft), doc, Contract.load(ROOT), PROVENANCE)
    draft = copy.deepcopy(baseline_draft)
    draft["actors"].pop()
    draft["mentions"].pop()
    draft["mentions"][0]["evidence"]["occurrence"] = None
    draft["mentions"][1]["evidence"]["unit_id"] = "u0"
    draft["contexts"] = [
        {
            "id": "c",
            "kind": "unknown",
            "attributed_actor_ids": [],
            "parent_context_id": None,
            "evidence_units": ["u0"],
            "roles": [],
            "statements": [],
            "events": [
                {
                    "id": "e",
                    "event_type": "other",
                    "polarity": "negated",
                    "participants": [{"actor_id": "a", "role": "other"}],
                    "evidence_units": ["u0"],
                    "statements": [],
                }
            ],
        }
    ]
    plan = build_plan(doc, draft, baseline, CONTRACT)
    choices = {
        "selector_choices": [
            {
                "item_id": x["item_id"],
                "choice_id": "p0",
                "action": "locate",
                "duplicate_of": None,
                "span_start": None,
                "span_end": None,
                "mention_kind": None,
                "reason": "source",
            }
            for x in plan["selector_items"]
        ],
        "coverage_choices": [
            {
                "item_id": x["item_id"],
                "action": "covered" if i < 2 else "add",
                "actor_id": None,
                "actor_type": None if i < 2 else "unknown",
                "mention_kind": None if i < 2 else "description",
                "reason": "source",
            }
            for i, x in enumerate(plan["coverage_items"])
        ],
    }
    return doc, draft, baseline, plan, choices


def test_explicit_choices_ground_unicode_and_preserve_original_and_negation():
    doc, draft, _, plan, choices = fixture()
    before = copy.deepcopy(draft)
    assert len(plan["selector_items"][0]["choices"]) == 2
    patched, counts = apply_choices(draft, plan, choices, CONTRACT)
    assert draft == before
    assert patched["contexts"] == before["contexts"]
    assert counts == {"selector_changed": 2, "coverage_covered": 2, "coverage_add": 1}
    result = contextual.materialize(json.dumps(patched), doc, CONTRACT, PROVENANCE)
    assert validate_record(result, doc, CONTRACT) == []
    assert result["mentions"][0]["evidence"]["end"] == 2
    assert result["events"][0]["polarity"] == "negated"
    assert len(result["actors"]) == 3
    assert "HIDDEN_QA" not in json.dumps(plan)


@pytest.mark.parametrize(
    "mutation,code",
    [
        (
            lambda c: c["selector_choices"][0].update(choice_id="outside"),
            "repair_choice_outside_catalog",
        ),
        (lambda c: c["selector_choices"].pop(), "repair_item_coverage_mismatch"),
        (
            lambda c: c["selector_choices"].append(c["selector_choices"][0]),
            "repair_item_coverage_mismatch",
        ),
        (
            lambda c: c["coverage_choices"][-1].update(actor_id="missing", actor_type=None),
            "repair_actor_assignment_invalid",
        ),
        (
            lambda c: c["coverage_choices"][-1].update(actor_type=None),
            "repair_new_actor_type_required",
        ),
        (lambda c: c.update(events=[]), "repair_schema_invalid"),
        (
            lambda c: c["coverage_choices"][-1].update(
                action="covered", actor_type=None, mention_kind=None
            ),
            "repair_coverage_not_actually_covered",
        ),
        (
            lambda c: c["coverage_choices"][0].update(
                action="add", actor_type="unknown", mention_kind="name"
            ),
            "repair_add_overlaps_existing",
        ),
    ],
)
def test_invalid_patch_does_not_silently_pass(mutation, code):
    _, draft, _, plan, choices = fixture()
    mutation(choices)
    with pytest.raises(DraftError, match=code):
        apply_choices(draft, plan, choices, CONTRACT)


def test_abstention_retains_unresolved_status_and_input():
    _, draft, _, plan, choices = fixture()
    before = copy.deepcopy(draft)
    choices["selector_choices"][0].update(choice_id=None, action="uncertain")
    choices["coverage_choices"][0]["action"] = "uncertain"
    with pytest.raises(GroundingError) as raised:
        apply_choices(draft, plan, choices, CONTRACT)
    assert {i["code"] for i in raised.value.issues} == {
        "selector_unresolved",
        "coverage_unresolved",
    }
    assert draft == before


def test_draft_cannot_change_after_catalog_creation():
    _, draft, _, plan, choices = fixture()
    draft["contexts"][0]["events"][0]["polarity"] = "affirmed"
    with pytest.raises(ValueError, match="Draft changed"):
        apply_choices(draft, plan, choices, CONTRACT)


def saved_fixture(saved_workspace):
    root, docs, config_path = saved_workspace
    for name in (
        "ontology/private-lending-v0.2.json",
        "schemas/extraction-v0.2.schema.json",
        "prompts/qwen-extraction-v6.txt",
        "prompts/qwen-mention-repair-v3.txt",
    ):
        (root / name).write_bytes((ROOT / name).read_bytes())
    doc, draft, baseline, _, choices = fixture()
    docs.write_text(json.dumps([doc]))
    config = json.loads(config_path.read_text())
    config["version"] = "qwen-extraction-v6"
    config_path.write_text(json.dumps(config))

    class Client(SimulatedClient):
        def call(self, path, body=None, timeout=15):
            if path == "/v1/chat/completions":
                self.paths.append(path)
                value = choices if "selector_items" in body["messages"][-1]["content"] else draft
                return response(
                    {
                        "model": MODEL,
                        "choices": [
                            {"finish_reason": "stop", "message": {"content": json.dumps(value)}}
                        ],
                    }
                )
            return super().call(path, body, timeout)

    run = root / "artifacts/original"
    result = run_extraction(root, [docs], config_path, run, Client())
    assert result["document_status_counts"] == {"draft_grounding_failed": 1}
    baseline_dir = root / "artifacts/baseline"
    baseline_dir.mkdir()
    baseline_path = baseline_dir / "candidates.jsonl"
    baseline_path.write_text(json.dumps(baseline) + "\n")
    (baseline_dir / "report.json").write_text(
        json.dumps(
            {
                "completed": True,
                "outputs_sha256": {"candidates.jsonl": sha256(baseline_path.read_bytes())},
            }
        )
    )
    config["version"] = "qwen-mention-repair-v3"
    config_path.write_text(json.dumps(config))
    return root, docs, config_path, run, baseline_path, Client


def test_saved_local_repair_retains_lineage_and_rejects_tampering(workspace):  # noqa: F811
    root, docs, config, run, baseline, Client = saved_fixture(workspace)
    old_hash = sha256((run / "rpc-007.response.bin").read_bytes())
    result = run_repair(root, [docs], run, baseline, config, root / "artifacts/repaired", Client())
    assert result["requests_submitted"] == 1
    assert result["document_status_counts"] == {"accepted_candidate": 1}
    assert result["repair_lineage"]["previous_candidates_are_gold"] is False
    assert sha256((run / "rpc-007.response.bin").read_bytes()) == old_hash
    baseline.write_text(baseline.read_text() + " ")
    client = Client()
    with pytest.raises(ValueError, match="Baseline candidates"):
        run_repair(root, [docs], run, baseline, config, root / "artifacts/bad", client)
    assert client.paths == []
    assert not (root / "artifacts/bad").exists()


def test_uncertain_network_failure_retains_run_and_does_not_retry(workspace):  # noqa: F811
    root, docs, config, run, baseline, _ = saved_fixture(workspace)
    client = SimulatedClient("timeout")
    result = run_repair(root, [docs], run, baseline, config, root / "artifacts/timed-out", client)
    assert result["requests_submitted"] == 1
    assert result["document_status_counts"] == {"transport_failed": 1}
    assert client.paths.count("/v1/chat/completions") == 1


def test_duplicates_require_explicit_same_actor_same_text_target():
    doc, draft, baseline, _, _ = fixture()
    for identifier, actor, unit, text in (
        ("known_a", "a", "u0", "A🙂"),
        ("known_b", "b", "u1", "B"),
        ("extra_b", "b", "u1", "B"),
    ):
        draft["mentions"].append(
            {
                "id": identifier,
                "actor_id": actor,
                "kind": "description",
                "evidence": {"unit_id": unit, "text": text, "occurrence": 0},
            }
        )
    original = copy.deepcopy(draft)
    plan = build_plan(doc, draft, baseline, CONTRACT)
    assert len(plan["selector_items"]) == 3
    assert plan["selector_items"][-1]["failure_kind"] == "duplicate_position"
    assert [x["occurrence"] for x in plan["selector_items"][0]["choices"]] == [1]
    choices = {
        "selector_choices": [
            {
                "item_id": i["item_id"],
                "action": "duplicate",
                "choice_id": None,
                "duplicate_of": i["duplicate_targets"][0]["mention_id"],
                "span_start": None,
                "span_end": None,
                "mention_kind": None,
                "reason": "same metadata occurrence",
            }
            for i in plan["selector_items"]
        ],
        "coverage_choices": [
            {
                "item_id": "h0",
                "action": "add",
                "actor_id": None,
                "actor_type": "unknown",
                "mention_kind": "description",
                "reason": "metadata",
            }
        ],
    }
    patched, counts = apply_choices(draft, plan, choices, CONTRACT)
    assert counts["explicit_duplicate_removed"] == 3
    assert draft == original and patched["contexts"] == original["contexts"]
    assert {m["id"] for m in patched["mentions"]} == {"known_a", "known_b", "repair_m0"}
    record = contextual.materialize(json.dumps(patched), doc, CONTRACT, PROVENANCE)
    assert validate_record(record, doc, CONTRACT) == []
    choices["selector_choices"][0]["duplicate_of"] = "known_b"
    with pytest.raises(DraftError, match="repair_duplicate_target_invalid"):
        apply_choices(draft, plan, choices, CONTRACT)


def test_uncertain_choice_cannot_smuggle_a_location():
    _, draft, _, plan, choices = fixture()
    choices["selector_choices"][0]["action"] = "uncertain"
    with pytest.raises(DraftError, match="repair_unused_choice"):
        apply_choices(draft, plan, choices, CONTRACT)


@pytest.mark.parametrize("end", [2, 0, 999, True])
def test_local_span_uses_original_unicode_characters_and_rejects_bad_bounds(end):
    doc = document(
        "metadata",
        {"record_pointer": "/0"},
        {"field": ("/0/f", "A🙂。")},
        family_id="family:0",
        allocation="development_exposed",
    )
    draft = {
        "actors": [{"id": "a", "actor_type": "unknown"}],
        "contexts": [],
        "mentions": [
            {
                "id": "m",
                "actor_id": "a",
                "kind": "name",
                "evidence": {"unit_id": "u0", "text": "UNSUPPORTED", "occurrence": None},
            }
        ],
    }
    prior = copy.deepcopy(draft)
    prior["mentions"][0]["evidence"]["text"] = "A🙂"
    baseline = typed.materialize(json.dumps(prior), doc, Contract.load(ROOT), PROVENANCE)
    plan = build_plan(doc, draft, baseline, CONTRACT)
    choices = {
        "selector_choices": [
            {
                "item_id": "s0",
                "action": "local_span",
                "choice_id": None,
                "duplicate_of": None,
                "span_start": 0,
                "span_end": end,
                "mention_kind": "description",
                "reason": "metadata source substring",
            }
        ],
        "coverage_choices": [
            {
                "item_id": "h0",
                "action": "covered",
                "actor_id": None,
                "actor_type": None,
                "mention_kind": None,
                "reason": "explicit position",
            }
        ],
    }
    if type(end) is not int or end != 2:
        with pytest.raises(DraftError):
            apply_choices(draft, plan, choices, CONTRACT)
        return
    patched, counts = apply_choices(draft, plan, choices, CONTRACT)
    assert patched["mentions"][0]["evidence"]["text"] == "A🙂"
    assert patched["mentions"][0]["kind"] == "description"
    assert draft["mentions"][0]["evidence"]["text"] == "UNSUPPORTED"
    assert counts["explicit_local_substring_changed"] == 1
    assert (
        validate_record(
            contextual.materialize(json.dumps(patched), doc, CONTRACT, PROVENANCE), doc, CONTRACT
        )
        == []
    )
