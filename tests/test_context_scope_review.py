"""Metadata-only context moves, source boundaries, conservation and offline replay."""

import copy
import json
from pathlib import Path

import pytest
from test_anchored import PROVENANCE
from test_extraction import MODEL, SimulatedClient, response, workspace  # noqa: F401
from test_extraction import source as extra_source

from judicial_kg.contracts import Contract
from judicial_kg.extraction import context_scope_review as review
from judicial_kg.extraction import contextual
from judicial_kg.extraction.anchored import GroundingError
from judicial_kg.extraction.draft import DraftError
from judicial_kg.extraction.run import run_extraction
from judicial_kg.sources.records import document, sha256
from judicial_kg.validation import validate_record

ROOT = Path(__file__).resolve().parents[1]
CONTRACT = Contract.load(ROOT, "0.2.0")


def fixture():
    doc = document(
        "metadata",
        {"record_pointer": "/0"},
        {"header": ("/0/header", "A。"), "footer": ("/0/footer", "B。C。")},
        family_id="metadata:family",
        allocation="development_exposed",
        reference_labels=[{"answer": "HIDDEN_QA"}],
    )
    draft = {
        "actors": [{"id": i, "actor_type": "person"} for i in "abc"],
        "mentions": [
            {
                "id": "m" + i,
                "actor_id": i,
                "kind": "name",
                "evidence": {"unit_id": "u" + str(n), "text": i.upper(), "occurrence": None},
            }
            for n, i in enumerate("abc")
        ],
        "contexts": [
            {
                "id": "ctx",
                "kind": "procedural_record",
                "attributed_actor_ids": ["a"],
                "parent_context_id": None,
                "evidence_units": ["u0"],
                "roles": [
                    {
                        "id": "r" + i,
                        "actor_id": i,
                        "role": "other",
                        "polarity": "affirmed",
                        "evidence_units": ["u" + str(n)],
                    }
                    for n, i in enumerate("abc")
                ],
                "events": [],
                "statements": [],
            }
        ],
    }
    return doc, draft


def choices(plan):
    return {
        "scope_choices": [
            {
                "item_id": i["item_id"],
                "reason": "metadata source scope",
                "action": "rehome",
                "choice_id": i["choices"][0]["choice_id"],
            }
            for i in plan["scope_items"]
        ]
    }


def test_footer_siblings_do_not_widen_original_and_preserve_all_role_content():
    doc, draft = fixture()
    original = copy.deepcopy(draft)
    plan = review.build_plan(doc, draft, CONTRACT)
    patched, ledger = review.apply_choices(doc, draft, plan, choices(plan), CONTRACT)
    assert draft == original and "HIDDEN_QA" not in json.dumps(plan)
    assert patched["contexts"][0]["evidence_units"] == ["u0"]
    assert len(patched["contexts"]) == 3
    assert len(ledger) == 2 and all(x["assertion_before"] == x["assertion_after"] for x in ledger)
    record = contextual.materialize(json.dumps(patched), doc, CONTRACT, PROVENANCE)
    assert validate_record(record, doc, CONTRACT) == []
    assert len(record["roles"]) == 3


def test_shared_footer_source_creates_one_sibling_without_duplicate_context_ids():
    doc, draft = fixture()
    draft["contexts"][0]["roles"][2]["evidence_units"] = ["u1"]
    draft["mentions"][2]["evidence"].update(unit_id="u1", text="B")
    plan = review.build_plan(doc, draft, CONTRACT)
    patched, _ = review.apply_choices(doc, draft, plan, choices(plan), CONTRACT)
    assert len(patched["contexts"]) == 2 and len(patched["contexts"][1]["roles"]) == 2


@pytest.mark.parametrize(
    "mutation",
    [
        lambda d: d["scope_choices"].pop(),
        lambda d: d["scope_choices"].append(d["scope_choices"][0]),
        lambda d: d["scope_choices"][0].update(choice_id="outside"),
        lambda d: d["scope_choices"][0].update(action="uncertain"),
        lambda d: d["scope_choices"][0].update(actor_id="another"),
        lambda d: d["scope_choices"][0].update(reason="x" * 81),
    ],
)
def test_invalid_decisions_leave_original_immutable(mutation):
    doc, draft = fixture()
    old = copy.deepcopy(draft)
    plan = review.build_plan(doc, draft, CONTRACT)
    ds = choices(plan)
    mutation(ds)
    with pytest.raises(DraftError):
        review.apply_choices(doc, draft, plan, ds, CONTRACT)
    assert draft == old


def test_choice_from_other_item_catalog_rejected():
    doc, draft = fixture()
    plan = review.build_plan(doc, draft, CONTRACT)
    ds = choices(plan)
    ds["scope_choices"][0]["choice_id"] = ds["scope_choices"][1]["choice_id"]
    with pytest.raises(DraftError, match="outside_item_catalog"):
        review.apply_choices(doc, draft, plan, ds, CONTRACT)


def test_uncertain_keeps_failure_and_catalog_and_draft_tampering_rejected():
    doc, draft = fixture()
    plan = review.build_plan(doc, draft, CONTRACT)
    ds = choices(plan)
    ds["scope_choices"][0].update(action="uncertain", choice_id=None)
    with pytest.raises(GroundingError, match="draft_grounding_failed"):
        review.apply_choices(doc, draft, plan, ds, CONTRACT)
    hacked = copy.deepcopy(plan)
    hacked["scope_items"][0]["choices"][0]["context"]["kind"] = "court_finding"
    with pytest.raises(ValueError, match="catalog"):
        review.apply_choices(doc, draft, hacked, choices(plan), CONTRACT)
    draft["contexts"][0]["roles"][0]["polarity"] = "negated"
    with pytest.raises(ValueError, match="frozen"):
        review.apply_choices(doc, draft, plan, choices(plan), CONTRACT)


@pytest.mark.parametrize(
    "mutation",
    [
        lambda d: d["contexts"][0].update(kind="party_claim"),
        lambda d: d["contexts"][0].update(parent_context_id="parent"),
        lambda d: d["actors"][1].update(actor_type="organization"),
        lambda d: d["mentions"][1]["evidence"].update(text="absent"),
    ],
)
def test_footer_split_has_no_proposal_for_unqualified_sources(mutation):
    doc, draft = fixture()
    mutation(draft)
    assert review.build_plan(doc, draft, CONTRACT)["scope_items"][0]["choices"] == []


def test_existing_event_context_requires_same_speaker_kind_parent_and_all_property_units():
    doc, draft = fixture()
    c = draft["contexts"][0]
    c["roles"] = []
    c.update(kind="party_claim", evidence_units=["u0"])
    event = {
        "id": "e",
        "event_type": "other",
        "polarity": "negated",
        "participants": [{"actor_id": "b", "role": "other"}],
        "evidence_units": ["u1"],
        "statements": [
            {
                "id": "s",
                "predicate": "other",
                "polarity": "affirmed",
                "value": {
                    "kind": "text",
                    "source": {"unit_id": "u2", "text": "C", "occurrence": None},
                },
            }
        ],
    }
    c["events"] = [event]
    target = copy.deepcopy(c)
    target.update(id="target", events=[], evidence_units=["u1", "u2"])
    draft["contexts"].append(target)
    plan = review.build_plan(doc, draft, CONTRACT)
    assert len(plan["scope_items"][0]["choices"]) == 1
    patched, ledger = review.apply_choices(doc, draft, plan, choices(plan), CONTRACT)
    assert patched["contexts"][1]["events"] == [event]
    assert ledger[0]["assertion_before"] == ledger[0]["assertion_after"]
    assert (
        validate_record(
            contextual.materialize(json.dumps(patched), doc, CONTRACT, PROVENANCE), doc, CONTRACT
        )
        == []
    )
    for key, value in [
        ("attributed_actor_ids", ["b"]),
        ("kind", "court_finding"),
        ("parent_context_id", "c"),
        ("evidence_units", ["u1"]),
    ]:
        changed = copy.deepcopy(draft)
        changed["contexts"][1][key] = value
        assert review.build_plan(doc, changed, CONTRACT)["scope_items"][0]["choices"] == []


def test_complete_source_pilot_replay_and_manifest_response_hash_tampering(workspace):  # noqa: F811
    root, docs, config = workspace
    for name in [
        "ontology/private-lending-v0.2.json",
        "schemas/extraction-v0.2.schema.json",
        "prompts/qwen-extraction-v9.txt",
        review.ContextScopeReview.PROMPT_FILE,
    ]:
        (root / name).write_bytes((ROOT / name).read_bytes())
    doc, draft = fixture()
    docs.write_text(json.dumps([doc, extra_source(2)]))
    plan = review.build_plan(doc, draft, CONTRACT)
    ds = choices(plan)
    nested = copy.deepcopy(draft)
    mentions = nested.pop("mentions")
    for a in nested["actors"]:
        a["mentions"] = [
            {k: v for k, v in m.items() if k != "actor_id"}
            for m in mentions
            if m["actor_id"] == a["id"]
        ]
    cfg = json.loads(config.read_text())
    cfg.update(version="qwen-extraction-v9", max_documents=1)
    config.write_text(json.dumps(cfg))

    class Client(SimulatedClient):
        def call(self, path, body=None, timeout=15):
            if path != "/v1/chat/completions":
                return super().call(path, body, timeout)
            self.paths.append(path)
            assert "schema" in body["response_format"]
            value = ds if "scope_items" in body["messages"][-1]["content"] else nested
            assert "HIDDEN_QA" not in json.dumps(body)
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
    cfg["version"] = review.VERSION
    config.write_text(json.dumps(cfg))
    out = root / "artifacts/review"
    client = Client()
    report = review.run_repair(root, [docs], spec, config, out, client)
    assert report["document_status_counts"] == {"accepted_candidate": 1, "not_selected": 1}
    assert client.paths.count("/v1/chat/completions") == 1
    replay = root / "artifacts/replay"
    proof = review.replay_repair(root, [docs], spec, out, replay)
    assert (
        proof["requests_submitted"] == 0
        and proof["accepted_candidates_match_saved"]
        and proof["outcomes_match_saved"]
    )
    assert sorted(p.read_bytes() for p in out.glob("scope-ledger-*")) == sorted(
        p.read_bytes() for p in replay.glob("scope-ledger-*")
    )
    call = [c for c in report["response_metadata"] if c["path"] == "/v1/chat/completions"][0]
    request_path = out / f"rpc-{call['rpc']:03}.request.json"
    old_request, old_report = request_path.read_bytes(), (out / "report.json").read_bytes()
    tampered = json.loads(old_request)
    tampered["body"]["response_format"] = {"type": "json_object"}
    request_path.write_text(json.dumps(tampered))
    changed_report = json.loads(old_report)
    changed_report["outputs_sha256"][request_path.name] = sha256(request_path.read_bytes())
    (out / "report.json").write_text(json.dumps(changed_report))
    with pytest.raises(ValueError, match="generation constraints"):
        review.replay_repair(root, [docs], spec, out, root / "artifacts/grammar-tampered")
    request_path.write_bytes(old_request)
    (out / "report.json").write_bytes(old_report)
    original_bytes = docs.read_bytes()
    docs.write_bytes(original_bytes + b" ")
    with pytest.raises(ValueError):
        review.replay_repair(root, [docs], spec, out, root / "artifacts/tampered")
    docs.write_bytes(original_bytes)
    call = [c for c in report["response_metadata"] if c["path"] == "/v1/chat/completions"][0]
    response_path = out / f"rpc-{call['rpc']:03}.response.bin"
    response_path.write_bytes(response_path.read_bytes() + b" ")
    with pytest.raises(ValueError, match="hash mismatch"):
        review.replay_repair(root, [docs], spec, out, root / "artifacts/response-tampered")
