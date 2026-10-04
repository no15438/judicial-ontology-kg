"""Metadata-only fixtures test grounding and nesting without invented legal cases."""

import copy
import json
from pathlib import Path

import pytest

from judicial_kg.contracts import Contract
from judicial_kg.extraction import anchored
from judicial_kg.extraction.draft import DraftError
from judicial_kg.sources.records import document, verify_evidence
from judicial_kg.validation import validate_record

ROOT = Path(__file__).resolve().parents[1]
CONTRACT = Contract.load(ROOT)
PROVENANCE = {"method": "model", "producer": "unit-simulation", "version": "unit", "run_id": "unit"}


def source():
    return document(
        "metadata",
        {"record_pointer": "/0"},
        {"alpha": ("/0/a", "A🙂，B 42。C；A🙂。 "), "beta": ("/0/b", "1,000\n")},
        family_id="metadata:0",
        allocation="development_exposed",
        reference_labels=[{"answer": "HIDDEN_QA"}],
    )


def draft():
    return {
        "actors": [{"id": "a", "actor_type": "unknown"}],
        "mentions": [
            {
                "id": "m",
                "actor_id": "a",
                "kind": "description",
                "evidence": {"unit_id": "u0", "text": "A🙂", "occurrence": None},
            }
        ],
        "contexts": [
            {
                "id": "c",
                "kind": "unknown",
                "attributed_actor_id": None,
                "parent_context_id": None,
                "evidence_units": ["u0", "u1"],
                "roles": [
                    {
                        "id": "r",
                        "actor_id": "a",
                        "role": "other",
                        "polarity": "uncertain",
                        "evidence_units": ["u0"],
                    }
                ],
                "events": [
                    {
                        "id": "e",
                        "event_type": "other",
                        "polarity": "uncertain",
                        "participants": [{"actor_id": "a", "role": "other"}],
                        "evidence_units": ["u1"],
                        "statements": [
                            {
                                "id": "s",
                                "predicate": "other",
                                "polarity": "uncertain",
                                "value": {
                                    "kind": "text",
                                    "source": {"unit_id": "u1", "text": "42", "occurrence": None},
                                    "normalized": None,
                                    "unit": None,
                                },
                            }
                        ],
                    }
                ],
                "statements": [],
            }
        ],
    }


def compile_value(value):
    return anchored.materialize(json.dumps(value), source(), CONTRACT, PROVENANCE)


def test_lossless_units_cover_all_fields_with_unicode_and_numeric_commas():
    doc = source()
    units = anchored.source_units(doc)
    for field in doc["fields"]:
        local = [u for u in units if u["field"] == field["name"]]
        assert "".join(u["text"] for u in local) == field["text"]
        assert local[0]["start"] == 0 and local[-1]["end"] == len(field["text"])
        assert all(a["end"] == b["start"] for a, b in zip(local, local[1:], strict=False))
    assert units[-1]["text"] == "1,000\n"
    damaged = copy.deepcopy(doc)
    damaged["fields"][0]["text"] += "changed"
    with pytest.raises(ValueError):
        anchored.source_units(damaged)


def test_nesting_generates_links_and_values_and_keeps_final_contract_unchanged():
    value = compile_value(draft())
    assert value["events"][0]["context_id"] == "c"
    assert value["statements"][0]["context_id"] == "c"
    assert value["statements"][0]["subject_id"] == "e"
    assert value["statements"][0]["value"]["raw"] == "42"
    assert value["actors"][0]["mention_ids"] == ["m"]
    assert value["reference_status"] == "not_graph_gold"
    assert validate_record(value, source(), CONTRACT) == []
    verify_evidence(source(), value["mentions"][0]["evidence"])


def test_duplicate_name_across_units_is_disambiguated_without_choosing_first():
    value = draft()
    value["mentions"].append(
        {
            "id": "m2",
            "actor_id": "a",
            "kind": "description",
            "evidence": {"unit_id": "u3", "text": "A🙂", "occurrence": None},
        }
    )
    compiled = compile_value(value)
    first, second = [m["evidence"] for m in compiled["mentions"]]
    assert first["quote"] == second["quote"] and first["start"] < second["start"]
    assert validate_record(compiled, source(), CONTRACT) == []


def test_wrong_field_unknown_unit_outside_context_and_missing_value_all_reported():
    value = draft()
    value["mentions"][0]["evidence"]["unit_id"] = "u5"
    value["contexts"][0]["roles"][0]["evidence_units"] = ["u2", "u999"]
    value["contexts"][0]["events"][0]["statements"][0]["value"]["source"]["text"] = "999"
    with pytest.raises(anchored.GroundingError) as raised:
        compile_value(value)
    codes = [i["code"] for i in raised.value.issues]
    assert codes.count("quote_missing_in_unit") == 2
    assert "unknown_evidence_unit" in codes and "evidence_outside_context" in codes
    assert "999" not in str(raised.value)


def test_model_cannot_override_inherited_context_or_event_subject():
    for key in ("subject_id", "context_id", "evidence"):
        value = draft()
        value["contexts"][0]["events"][0]["statements"][0][key] = "invented"
        with pytest.raises(anchored.GroundingError) as raised:
            compile_value(value)
        assert all(i["code"] == "draft_schema_invalid" for i in raised.value.issues)


def test_actor_property_cannot_refer_to_an_event_outside_its_context():
    value = draft()
    prop = copy.deepcopy(value["contexts"][0]["events"][0]["statements"][0])
    prop.update(id="s2", subject_id="e")
    value["contexts"][0]["statements"].append(prop)
    with pytest.raises(anchored.GroundingError) as raised:
        compile_value(value)
    assert raised.value.issues[0]["code"] == "context_statement_subject_must_be_actor"


def test_final_ontology_checks_still_reject_illegal_attribution():
    value = draft()
    value["contexts"][0]["kind"] = "party_claim"
    compiled = compile_value(value)
    assert "party_attribution_required" in {
        i["code"] for i in validate_record(compiled, source(), CONTRACT)
    }


def test_prompt_only_contains_source_units_not_answers_or_labels():
    messages = anchored.messages(source(), "instructions", anchored.draft_schema(CONTRACT))
    assert "HIDDEN_QA" not in json.dumps(messages)
    payload = json.loads(messages[1]["content"])
    assert all(set(u) == {"id", "field", "text"} for u in payload["source_units"])
    with pytest.raises(DraftError, match="draft_invalid_json"):
        anchored.materialize("```{} ```", source(), CONTRACT, PROVENANCE)
