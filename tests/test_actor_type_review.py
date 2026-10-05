"""Metadata court-name/type tests, not case material or independent gold."""

import copy
import json
from pathlib import Path

import pytest
from test_extraction import MODEL, SimulatedClient, response, source, workspace  # noqa: F401
from test_selector_repair import CONTRACT, decision

from judicial_kg.extraction import actor_type_review, local_review, selector_repair
from judicial_kg.extraction.anchored import GroundingError
from judicial_kg.extraction.draft import DraftError
from judicial_kg.extraction.run import run_extraction
from judicial_kg.sources.records import document


def fixture():
    doc = document(
        "metadata",
        {"record_pointer": "/0"},
        {"field": ("/0/field", "元数据法院，B。")},
        family_id="metadata:family",
        allocation="development_exposed",
    )
    draft = {
        "actors": [{"id": "a", "actor_type": "person"}, {"id": "b", "actor_type": "unknown"}],
        "mentions": [
            {
                "id": "ma",
                "actor_id": "a",
                "kind": "name",
                "evidence": {"unit_id": "u0", "text": "元数据法院", "occurrence": None},
            },
            {
                "id": "mb",
                "actor_id": "b",
                "kind": "name",
                "evidence": {"unit_id": "u1", "text": "B", "occurrence": None},
            },
        ],
        "contexts": [
            {
                "id": "c",
                "kind": "court_finding",
                "attributed_actor_ids": ["a"],
                "parent_context_id": None,
                "evidence_units": ["u0", "u1"],
                "roles": [],
                "events": [],
                "statements": [],
            }
        ],
    }
    plan = actor_type_review.build_plan(doc, draft, CONTRACT)
    ds = {
        "actor_type_choices": [
            {"actor_id": "a", "action": "classify", "actor_type": "court", "reason": "metadata"}
        ]
    }
    return doc, draft, plan, ds


def test_only_type_changes_and_court_name_is_grounded():
    _, draft, plan, ds = fixture()
    old = copy.deepcopy(draft)
    patched = actor_type_review.apply_choices(draft, plan, ds)
    assert draft == old
    assert patched["actors"][0] == {"id": "a", "actor_type": "court"}
    patched["actors"][0]["actor_type"] = "person"
    assert patched == old
    assert plan["actor_type_items"][0]["court_name_evidence"][0]["text"] == "元数据法院"


@pytest.mark.parametrize(
    "mutation",
    [
        lambda ds: ds["actor_type_choices"].clear(),
        lambda ds: ds["actor_type_choices"].append(ds["actor_type_choices"][0]),
        lambda ds: ds["actor_type_choices"][0].update(actor_id="b"),
        lambda ds: ds["actor_type_choices"][0].update(actor_type="organization"),
        lambda ds: ds["actor_type_choices"][0].update(attributed_actor_ids=["b"]),
    ],
)
def test_outside_catalog_changes_rejected(mutation):
    _, draft, plan, ds = fixture()
    mutation(ds)
    with pytest.raises(DraftError):
        actor_type_review.apply_choices(draft, plan, ds)


def test_uncertainty_and_frozen_source():
    _, draft, plan, ds = fixture()
    ds["actor_type_choices"][0].update(action="uncertain", actor_type=None)
    with pytest.raises(GroundingError):
        actor_type_review.apply_choices(draft, plan, ds)
    draft["actors"][0]["id"] = "changed"
    with pytest.raises(ValueError, match="frozen"):
        actor_type_review.apply_choices(draft, plan, ds)


def test_person_without_court_name_never_added_to_type_catalog():
    doc, draft, _, _ = fixture()
    draft["mentions"][0]["evidence"]["text"] = "absent"
    assert actor_type_review.build_plan(doc, draft, CONTRACT)["actor_type_items"] == []


def test_complete_parent_chain_replay_and_rejection_conservation(workspace):  # noqa: F811
    root, docs, config = workspace
    project = Path(__file__).resolve().parents[1]
    for name in [
        "ontology/private-lending-v0.2.json",
        "schemas/extraction-v0.2.schema.json",
        "prompts/qwen-extraction-v6.txt",
        "prompts/qwen-selector-repair-v1.txt",
        "prompts/qwen-local-review-v1.txt",
        "prompts/qwen-actor-type-review-v1.txt",
    ]:
        (root / name).write_bytes((project / name).read_bytes())
    doc, draft, _, ds = fixture()
    unsupported = copy.deepcopy(draft["mentions"][0])
    unsupported["id"] = "extra"
    unsupported["evidence"] = {"unit_id": "u1", "text": "元数据法院", "occurrence": None}
    draft["mentions"].append(unsupported)
    plan = selector_repair.build_plan(doc, draft, CONTRACT)
    selectors = {"selector_choices": [decision(i, "uncertain") for i in plan["selector_items"]]}
    local = {
        "context_choices": [
            {"context_id": "c", "action": "uncertain", "kind": None, "reason": "metadata court"}
        ],
        "mention_choices": [
            {
                "mention_id": "extra",
                "action": "reject_unsupported",
                "local_text": None,
                "kind": None,
                "reason": "metadata absent name",
            }
        ],
        "property_choices": [],
        "actor_choices": [],
    }
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
                if "actor_type_items" in msg
                else local
                if "property_items" in msg
                else selectors
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
    selector_run = root / "artifacts/selectors"
    selector_repair.run_repair(root, [docs], spec, config, selector_run, Client())
    cfg["version"] = local_review.VERSION
    config.write_text(json.dumps(cfg))
    local_run = root / "artifacts/local"
    local_review.run_repair(root, [docs], spec, selector_run, config, local_run, Client())
    cfg["version"] = actor_type_review.VERSION
    config.write_text(json.dumps(cfg))
    out = root / "artifacts/types"
    report = actor_type_review.run_repair(
        root, [docs], spec, selector_run, local_run, config, out, Client()
    )
    assert report["document_status_counts"] == {"accepted_candidate": 1, "not_selected": 1}
    replay = root / "artifacts/replay"
    proof = actor_type_review.replay_repair(
        root, [docs], spec, selector_run, local_run, out, replay
    )
    assert (
        proof["requests_submitted"] == 0
        and proof["accepted_candidates_match_saved"]
        and proof["outcomes_match_saved"]
    )
    assert (out / "parent-rejected-assertions.json").read_bytes() == (
        replay / "parent-rejected-assertions.json"
    ).read_bytes()
    assert (
        len(json.loads((out / "parent-rejected-assertions.json").read_text())[doc["document_id"]])
        == 1
    )
