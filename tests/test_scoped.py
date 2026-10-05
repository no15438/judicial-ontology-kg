"""Enclosing/direct source partition and quote isolation on metadata, not legal gold."""

import copy
import json
from pathlib import Path

import pytest
from test_anchored import PROVENANCE, source
from test_context_first import prepare
from test_extraction import MODEL, SimulatedClient, response, workspace  # noqa: F401
from test_identity_first import identity_fixtures

from judicial_kg.contracts import Contract
from judicial_kg.extraction import scoped
from judicial_kg.extraction.draft import DraftError
from judicial_kg.extraction.replay import replay_completed
from judicial_kg.extraction.run import run_extraction
from judicial_kg.graph.assembly import project_records, restore_records
from judicial_kg.graph.run import assemble_batch
from judicial_kg.sources.records import canonical_hash
from judicial_kg.validation import validate_record

ROOT = Path(__file__).resolve().parents[1]
CONTRACT = Contract.load(ROOT, "0.3.0")


def fixtures():
    header, _ = identity_fixtures()
    parent = header["contexts"][0]
    parent["enclosing_units"] = parent.pop("evidence_units")
    parent["assertion_units"] = ["u0"]
    child = copy.deepcopy(parent)
    child.update(
        id="quote",
        kind="quoted_material",
        attributed_actor_ids=["b"],
        parent_context_id=parent["id"],
        enclosing_units=["u1"],
        assertion_units=["u1"],
    )
    header["contexts"].append(child)
    return header


def record():
    return scoped.compile_checked(fixtures(), source(), CONTRACT, PROVENANCE)


def test_scope_roundtrip_preserves_both_span_roles_and_legacy_schema_bytes():
    before = [(ROOT / f"schemas/extraction-v0.{v}.schema.json").read_bytes() for v in (1, 2)]
    result = record()
    assert result["schema_version"] == "0.3.0"
    assert len(result["contexts"][0]["evidence"]) == 2
    assert len(result["contexts"][0]["assertion_evidence"]) == 1
    graph = project_records([result])
    assert {e["relation"] for e in graph["edges"]} >= {
        "covers",
        "assertion_source",
        "within_context",
    }
    assert restore_records(graph) == [result]
    assert before == [
        (ROOT / f"schemas/extraction-v0.{v}.schema.json").read_bytes() for v in (1, 2)
    ]
    assert validate_record(result, source(), Contract.load(ROOT, "0.2.0"))
    lost = copy.deepcopy(result)
    del lost["contexts"][0]["assertion_evidence"]
    assert validate_record(lost, source(), CONTRACT)
    batch = assemble_batch([source()], [json.dumps(result)], CONTRACT)
    assert batch["summary"]["documents_assembled"] == 1
    assert restore_records(batch["graph"]) == [result]
    assert batch["summary"]["validation"]["accepted_evidence_spans_replayed"] == 7


@pytest.mark.parametrize(
    "change,code",
    [
        (lambda h: h["contexts"][0].update(enclosing_units=["u0"]), "context_outside_parent"),
        (
            lambda h: h["contexts"][0].update(assertion_units=["u0", "u1"]),
            "assertion_overlaps_child_scope",
        ),
        (
            lambda h: h["contexts"][0].update(assertion_units=["u3"]),
            "assertion_outside_enclosing_scope",
        ),
        (
            lambda h: h["contexts"][0].update(enclosing_units=["u0", "u1", "u2"]),
            "enclosing_scope_not_partitioned",
        ),
        (
            lambda h: h["contexts"][1].update(assertion_units=[]),
            "leaf_context_without_assertion_scope",
        ),
        (lambda h: h["contexts"][1].update(parent_context_id="quote"), "context_cycle"),
    ],
)
def test_scope_errors_reject_without_widening_removing_or_reparenting(change, code):
    header = fixtures()
    change(header)
    before = canonical_hash(header)
    with pytest.raises(DraftError) as raised:
        scoped.compile_checked(header, source(), CONTRACT, PROVENANCE)
    assert code in {i["code"] for i in raised.value.issues}
    assert canonical_hash(header) == before


@pytest.mark.parametrize("kind", ["roles", "events", "statements"])
def test_assertions_cannot_use_child_quote_even_inside_parent_envelope(kind):
    header = fixtures()
    if kind == "roles":
        item = {
            "id": "r",
            "actor_id": "a",
            "role": "other",
            "polarity": "uncertain",
            "evidence_units": ["u1"],
        }
    elif kind == "events":
        item = {
            "id": "e",
            "event_type": "other",
            "polarity": "uncertain",
            "participants": [{"actor_id": "a", "role": "other"}],
            "evidence_units": ["u1"],
            "statements": [],
        }
    else:
        item = {
            "id": "s",
            "subject_id": "a",
            "predicate": "other",
            "polarity": "uncertain",
            "value": {"kind": "text", "source": {"unit_id": "u1", "text": "42", "occurrence": 0}},
        }
    header["contexts"][0][kind] = [item]
    with pytest.raises(DraftError) as raised:
        scoped.compile_checked(header, source(), CONTRACT, PROVENANCE)
    assert "evidence_outside_assertion_scope" in {i["code"] for i in raised.value.issues}
    # Same original assertion can be independently attributed to its declared quoted context.
    header["contexts"][0][kind] = []
    header["contexts"][1][kind] = [item]
    assert scoped.compile_checked(header, source(), CONTRACT, PROVENANCE)


def test_body_schema_and_actual_model_input_exclude_quoted_source():
    h = fixtures()
    schema = scoped.body_schema(h, h["contexts"][0], CONTRACT)
    assert schema["$defs"]["selector"]["properties"]["unit_id"]["enum"] == ["u0"]
    m = scoped.body_messages(source(), "prompt", h, h["contexts"][0], schema)
    payload = json.loads(m[-1]["content"])
    assert [u["id"] for u in payload["source_units"]] == ["u0"]
    assert payload["frozen_context"]["enclosing_units"] == ["u0", "u1"]
    assert payload["frozen_context"]["assertion_units"] == ["u0"]
    assert "HIDDEN_QA" not in json.dumps(m)
    bad = {
        "mentions": [
            {
                "id": "m",
                "actor_id": "b",
                "kind": "name",
                "evidence": {"unit_id": "u1", "text": "B", "occurrence": 0},
            }
        ],
        "roles": [],
        "events": [],
        "statements": [],
    }
    with pytest.raises(DraftError):
        scoped.context_first.check_schema(bad, schema)


@pytest.mark.parametrize("failure", [None, "scope", "body"])
def test_v12_runner_and_zero_call_replay_keep_entire_phase_boundary(workspace, failure):  # noqa: F811
    root, docs, config_path = prepare(workspace)
    for name in (
        "ontology/private-lending-v0.3.json",
        "schemas/extraction-v0.3.schema.json",
        scoped.PROMPT_FILE,
    ):
        (root / name).write_bytes((ROOT / name).read_bytes())
    cfg = json.loads(config_path.read_text())
    cfg.update(version="qwen-extraction-v12")
    config_path.write_text(json.dumps(cfg))

    class Client(SimulatedClient):
        completed = 0

        def call(self, path, body=None, timeout=15):
            if path != "/v1/chat/completions":
                return super().call(path, body, timeout)
            self.paths.append(path)
            if not self.completed:
                value = fixtures()
                if failure == "scope":
                    value["contexts"][0]["enclosing_units"] = ["u0"]
            else:
                value = {"mentions": [], "roles": [], "events": [], "statements": []}
                if self.completed == 1:
                    value["roles"] = [
                        {
                            "id": "r",
                            "actor_id": "a",
                            "role": "other",
                            "polarity": "uncertain",
                            "evidence_units": ["u0"],
                        }
                    ]
                    if failure == "body":
                        value["roles"][0]["evidence_units"] = ["u1"]
            self.completed += 1
            return response(
                {
                    "model": MODEL,
                    "choices": [
                        {"finish_reason": "stop", "message": {"content": json.dumps(value)}}
                    ],
                }
            )

    run = root / "artifacts/v12"
    result = run_extraction(root, [docs], config_path, run, Client())
    assert result["requests_submitted"] == {None: 3, "scope": 1, "body": 2}[failure]
    assert result["document_status_counts"] == {
        "accepted_candidate"
        if failure is None
        else "contract_rejected"
        if failure == "scope"
        else "draft_grounding_failed": 1
    }
    replay = replay_completed(root, [docs], run, root / "artifacts/v12-replay")
    assert replay["requests_submitted"] == 0
    assert replay["outcome_and_candidate_equality_verified"]
    if failure == "body":
        plan = json.loads((run / "stage-000-stage-plan.json").read_text())
        assert [c["status"] for c in plan["contexts"]] == ["failed", "not_attempted"]
        assert (run / "accepted.json").read_text().strip() == "[]"


def test_event_property_cannot_borrow_quoted_value_when_event_itself_is_direct():
    h = fixtures()
    h["contexts"][0]["events"] = [
        {
            "id": "e",
            "event_type": "other",
            "polarity": "uncertain",
            "participants": [{"actor_id": "a", "role": "other"}],
            "evidence_units": ["u0"],
            "statements": [
                {
                    "id": "s",
                    "predicate": "other",
                    "polarity": "uncertain",
                    "value": {
                        "kind": "text",
                        "source": {"unit_id": "u1", "text": "42", "occurrence": 0},
                    },
                }
            ],
        }
    ]
    with pytest.raises(DraftError) as raised:
        scoped.compile_checked(h, source(), CONTRACT, PROVENANCE)
    assert "evidence_outside_assertion_scope" in {i["code"] for i in raised.value.issues}


def test_container_without_direct_source_is_explicitly_skipped_and_child_is_generated():
    h = fixtures()
    h["contexts"][0].update(
        kind="unknown", attributed_actor_ids=[], enclosing_units=["u1"], assertion_units=[]
    )
    calls, saved = [], {}

    def infer(messages, fmt, key):
        calls.append(key)
        if key == "header":
            return json.dumps(h)
        payload = json.loads(messages[-1]["content"])
        assert [u["id"] for u in payload["source_units"]] == ["u1"]
        return json.dumps({"mentions": [], "roles": [], "events": [], "statements": []})

    def save(key, value):
        saved[key] = copy.deepcopy(value)

    result = scoped.extract(source(), "prompt", CONTRACT, PROVENANCE, infer, save)
    assert result["schema_version"] == "0.3.0"
    assert calls == ["header", "body-001"]
    assert saved["stage-plan"]["complete"]
    assert [c["status"] for c in saved["stage-plan"]["contexts"]] == [
        "structural_container_no_direct_source",
        "validated",
    ]
    assert "u0" in saved["stage-plan"]["unassigned_source_unit_ids"]


def test_direct_scope_raw_evidence_is_independently_verified():
    r = record()
    r["contexts"][0]["assertion_evidence"][0]["quote"] = "changed"
    assert "evidence_mismatch" in {i["code"] for i in validate_record(r, source(), CONTRACT)}
