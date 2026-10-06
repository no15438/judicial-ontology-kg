"""Synthetic contract fixtures, not legal documents or graph gold."""

import copy
import json

import pytest
from test_anchored import source
from test_object_routing import with_object
from test_scoped import ROOT

from judicial_kg.contracts import Contract, candidate_record
from judicial_kg.graph.assembly import project_records, restore_records
from judicial_kg.graph.run import assemble_batch
from judicial_kg.sources.records import evidence_span
from judicial_kg.validation import validate_record

CONTRACT = Contract.load(ROOT, "0.5.0")


def collective_record():
    _, r = with_object()
    r.update(schema_version="0.5.0", ontology_version="0.5.0")
    r["objects"], r["object_mentions"] = [], []
    span = evidence_span(source(), "alpha", 5, 7)
    r["collectives"] = [
        {
            "id": "g",
            "context_id": "quote",
            "collective_type": "people_group",
            "membership_status": "unknown",
            "mention_ids": ["gm"],
        }
    ]
    r["collective_mentions"] = [
        {"id": "gm", "collective_id": "g", "context_id": "quote", "evidence": span}
    ]
    r["collective_memberships"] = []
    r["events"][0]["participants"] = []
    r["collective_participations"] = [
        {
            "id": "gp",
            "collective_id": "g",
            "event_id": "e",
            "context_id": "quote",
            "role": "other",
            "polarity": "negated",
            "evidence": [span],
        }
    ]
    return r


def codes(r):
    return {x["code"] for x in validate_record(r, source(), CONTRACT)}


def test_unknown_members_roundtrip_and_do_not_expand_to_actors():
    r = collective_record()
    original = copy.deepcopy(r)
    assert not codes(r)
    result = assemble_batch([source()], [json.dumps(r)], CONTRACT)
    assert result["summary"]["documents_assembled"] == 1
    assert restore_records(result["graph"]) == [r]
    assert r == original
    assert not any(e["relation"] == "participant" for e in result["graph"]["edges"])
    assert result["summary"]["semantic_precision_recall_f1"] is None


def member(actor="b", identifier="membership"):
    return {
        "id": identifier,
        "collective_id": "g",
        "actor_id": actor,
        "context_id": "quote",
        "polarity": "affirmed",
        "evidence": [evidence_span(source(), "alpha", 5, 7)],
    }


def test_unknown_partial_and_explicit_are_distinct():
    r = collective_record()
    r["collective_memberships"] = [member()]
    assert "unknown_collective_has_members" in codes(r)
    r["collectives"][0]["membership_status"] = "partial"
    assert not codes(r)
    r["collectives"][0]["membership_status"] = "explicit"
    assert "explicit_collective_requires_multiple_members" in codes(r)
    r["collective_memberships"].append(member("a", "membership2"))
    assert not codes(r)
    assert restore_records(project_records([r])) == [r]


@pytest.mark.parametrize(
    "collection,field,value,expected",
    [
        ("collective_mentions", "collective_id", "missing", "unresolved_reference"),
        ("collective_mentions", "context_id", "c", "collective_context_mismatch"),
        ("collective_participations", "event_id", "missing", "unresolved_reference"),
        ("collective_participations", "polarity", "affirmed", "collective_event_polarity_mismatch"),
    ],
)
def test_broken_references_and_scope_are_rejected(collection, field, value, expected):
    r = collective_record()
    r[collection][0][field] = value
    assert expected in codes(r)


def test_collective_evidence_cannot_be_fabricated_or_move_outside_context():
    r = collective_record()
    r["collective_mentions"][0]["evidence"]["quote"] = "modified"
    assert "evidence_mismatch" in codes(r)
    r = collective_record()
    r["collective_mentions"][0]["evidence"] = evidence_span(source(), "alpha", 0, 1)
    assert "collective_evidence_outside_assertion_scope" in codes(r)


def test_group_cannot_impersonate_a_court_or_actor():
    r = collective_record()
    r["contexts"][1]["attributed_actor_ids"] = ["g"]
    assert "unresolved_reference" in codes(r)
    r = collective_record()
    r["events"][0]["participants"] = [{"actor_id": "g", "role": "other"}]
    assert "unresolved_reference" in codes(r)


def test_empty_event_and_duplicate_links_are_rejected():
    r = collective_record()
    r["collective_participations"] = []
    assert "event_participant_required" in codes(r)
    r = collective_record()
    r["collective_participations"].append({**r["collective_participations"][0], "id": "gp2"})
    assert "duplicate_collective_participation" in codes(r)


def test_empty_envelope_has_explicit_collective_collections():
    r = collective_record()
    empty = candidate_record(source(), r["provenance"], ["alpha"], "0.5.0")
    assert empty["collectives"] == empty["collective_memberships"] == []
    assert not list(CONTRACT.validator.iter_errors(empty))
