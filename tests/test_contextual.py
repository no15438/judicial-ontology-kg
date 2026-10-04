"""Multi-speaker metadata fixtures; these are not cases or legal gold."""

import copy
import json
from pathlib import Path

import pytest
from test_anchored import PROVENANCE, draft, source
from test_contracts import candidate
from test_contracts import source as contract_source
from test_extraction import MODEL, SimulatedClient, response, workspace  # noqa: F401

from judicial_kg.contracts import Contract
from judicial_kg.extraction import contextual
from judicial_kg.extraction.anchored import GroundingError
from judicial_kg.extraction.replay import replay_completed
from judicial_kg.extraction.run import run_extraction
from judicial_kg.graph.assembly import project_records, restore_records
from judicial_kg.sources.records import canonical_hash, evidence_span
from judicial_kg.validation import validate_record

ROOT = Path(__file__).resolve().parents[1]
CONTRACT = Contract.load(ROOT, "0.2.0")


def joint_record():
    doc = contract_source()
    record = candidate(doc)
    record.update(schema_version="0.2.0", ontology_version="0.2.0")
    record["actors"].append({"id": "b", "actor_type": "unknown", "mention_ids": ["mb"]})
    record["mentions"].append(
        {"id": "mb", "actor_id": "b", "kind": "name", "evidence": evidence_span(doc, "field", 3, 4)}
    )
    context = record["contexts"][0]
    del context["attributed_actor_id"]
    context.update(kind="party_defense", attributed_actor_ids=["b", "a"])
    return doc, record


def test_joint_speakers_roundtrip_keeps_order_and_negated_event():
    doc, record = joint_record()
    assert validate_record(record, doc, CONTRACT) == []
    graph = project_records([record])
    edges = [e for e in graph["edges"] if e["relation"] == "attributed_to"]
    assert len(edges) == 2
    assert sorted(e["index"] for e in edges) == [0, 1]
    assert restore_records(graph) == [record]
    assert restore_records(graph)[0]["events"][0]["polarity"] == "negated"
    damaged = copy.deepcopy(graph)
    damaged["edges"] = [e for e in damaged["edges"] if e["id"] != edges[0]["id"]]
    with pytest.raises(ValueError):
        restore_records(damaged)


@pytest.mark.parametrize(
    "ids,expected",
    [
        ([], "party_attribution_required"),
        (["a", "a"], "schema:uniqueItems"),
        (["a", "absent"], "unresolved_reference"),
        (None, "schema:type"),
    ],
)
def test_joint_attribution_rejects_empty_duplicate_missing_and_null(ids, expected):
    doc, record = joint_record()
    record["contexts"][0]["attributed_actor_ids"] = ids
    assert expected in {i["code"] for i in validate_record(record, doc, CONTRACT)}


def test_every_speaker_is_type_checked_not_just_first():
    doc, record = joint_record()
    record["actors"][0]["actor_type"] = "court"
    assert "party_attribution_required" in {
        i["code"] for i in validate_record(record, doc, CONTRACT)
    }
    record["contexts"][0]["kind"] = "court_finding"
    assert "court_attribution_type" in {i["code"] for i in validate_record(record, doc, CONTRACT)}
    record["actors"][1]["actor_type"] = "court"
    assert validate_record(record, doc, CONTRACT) == []
    record["contexts"][0]["attributed_actor_ids"] = []
    assert validate_record(record, doc, CONTRACT) == []


def test_nested_quote_has_own_speaker_and_does_not_promote_negation():
    doc, record = joint_record()
    parent = record["contexts"][0]
    parent.update(kind="court_report", attributed_actor_ids=[])
    child = copy.deepcopy(parent)
    child.update(
        id="quote", kind="quoted_material", attributed_actor_ids=["b"], parent_context_id="c"
    )
    record["contexts"].append(child)
    record["events"][0]["context_id"] = "quote"
    record["statements"][0]["context_id"] = "quote"
    assert validate_record(record, doc, CONTRACT) == []
    assert restore_records(project_records([record])) == [record]
    child["evidence"] = [evidence_span(doc, "field", 24, 25)]
    assert "context_outside_parent" in {i["code"] for i in validate_record(record, doc, CONTRACT)}


def test_legacy_contract_is_not_silently_upgraded_or_mixed():
    doc = contract_source()
    old = candidate(doc)
    before = canonical_hash(old)
    assert validate_record(old, doc, Contract.load(ROOT)) == []
    assert restore_records(project_records([old])) == [old]
    assert canonical_hash(old) == before
    assert validate_record(old, doc, CONTRACT)
    _, new = joint_record()
    assert validate_record(new, doc, Contract.load(ROOT))
    new["contexts"][0]["attributed_actor_id"] = "a"
    assert "schema:additionalProperties" in {i["code"] for i in validate_record(new, doc, CONTRACT)}


def joint_draft():
    value = draft()
    prop = value["contexts"][0]["events"][0]["statements"][0]["value"]
    prop.pop("normalized")
    prop.pop("unit")
    value["actors"].append({"id": "b", "actor_type": "unknown"})
    value["mentions"].append(
        {
            "id": "mb",
            "actor_id": "b",
            "kind": "name",
            "evidence": {"unit_id": "u1", "text": "B", "occurrence": None},
        }
    )
    value["mentions"].append(
        {
            "id": "ma2",
            "actor_id": "a",
            "kind": "description",
            "evidence": {"unit_id": "u3", "text": "A🙂", "occurrence": None},
        }
    )
    context = value["contexts"][0]
    del context["attributed_actor_id"]
    context.update(kind="party_defense", attributed_actor_ids=["a", "b"])
    return value


def test_v6_compiles_multiple_mentions_without_expanding_or_reassigning_them():
    value = joint_draft()
    record = contextual.materialize(json.dumps(value), source(), CONTRACT, PROVENANCE)
    assert record["schema_version"] == "0.2.0"
    assert record["actors"][0]["mention_ids"] == ["m", "ma2"]
    assert record["contexts"][0]["attributed_actor_ids"] == ["a", "b"]
    assert validate_record(record, source(), CONTRACT) == []
    value["mentions"][-1]["evidence"]["unit_id"] = "u0"
    with pytest.raises(GroundingError):
        contextual.materialize(json.dumps(value), source(), CONTRACT, PROVENANCE)


def test_v6_saved_run_replays_with_its_own_contract(workspace):  # noqa: F811
    root, docs, config_path = workspace
    for filename in (
        "ontology/private-lending-v0.2.json",
        "schemas/extraction-v0.2.schema.json",
        "prompts/qwen-extraction-v6.txt",
    ):
        (root / filename).write_bytes((ROOT / filename).read_bytes())
    docs.write_text(json.dumps([source()]))
    config = json.loads(config_path.read_text())
    config["version"] = "qwen-extraction-v6"
    config_path.write_text(json.dumps(config))

    class Client(SimulatedClient):
        def call(self, path, body=None, timeout=15):
            if path == "/v1/chat/completions":
                self.paths.append(path)
                return response(
                    {
                        "model": MODEL,
                        "choices": [
                            {
                                "finish_reason": "stop",
                                "message": {"content": json.dumps(joint_draft())},
                            }
                        ],
                    }
                )
            return super().call(path, body, timeout)

    run = root / "artifacts/v6"
    result = run_extraction(root, [docs], config_path, run, Client())
    assert result["ontology_version"] == "0.2.0"
    assert result["document_status_counts"] == {"accepted_candidate": 1}
    replay = root / "artifacts/replay"
    replay_result = replay_completed(root, [docs], run, replay)
    assert replay_result["requests_submitted"] == 0
    assert replay_result["document_status_counts"] == {"accepted_candidate": 1}
    assert json.loads((replay / "accepted.json").read_text()) == json.loads(
        (run / "accepted.json").read_text()
    )
