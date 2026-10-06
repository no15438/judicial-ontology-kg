"""Synthetic protocol fixtures, never legal gold; frozen real inputs are private."""

import copy
import json
from pathlib import Path

import pytest
from test_anchored import PROVENANCE, source

from judicial_kg.contracts import Contract
from judicial_kg.current.compiler import Compiler
from judicial_kg.current.protocol import schema
from judicial_kg.current.retrieval import graph_expand, index_documents, search
from judicial_kg.current.runner import run_document
from judicial_kg.current.source import batches, locate
from judicial_kg.extraction.transport import Response
from judicial_kg.graph.assembly import project_records, restore_records
from judicial_kg.sources.records import canonical_hash

ROOT = Path(__file__).resolve().parents[1]
CONTRACT = Contract.load(ROOT, "0.5.0")


def frame():
    return {
        "kind": "quoted_material",
        "units": [1],
        "speakers": [],
        "actors": [{"actor_type": "person", "mention": {"unit": 1, "text": "B", "occurrence": 0}}],
        "collectives": [
            {
                "collective_type": "people_group",
                "membership_status": "unknown",
                "mention": {"unit": 1, "text": "42", "occurrence": 0},
                "members": [],
            }
        ],
        "objects": [],
        "roles": [],
        "events": [
            {
                "event_type": "other",
                "polarity": "negated",
                "units": [1],
                "objects": [],
                "participants": [{"target": {"kind": "collective", "index": 0}, "role": "other"}],
            }
        ],
        "statements": [],
        "unresolved": [],
    }


def compiler():
    doc = source()
    units, _ = batches(doc)
    return Compiler(doc, units, CONTRACT, PROVENANCE), schema(
        CONTRACT, list(range(len(units))), list(range(len(units)))
    )


def test_collective_only_event_and_source_roundtrip_without_actor_expansion():
    c, s = compiler()
    c.context(frame(), s["properties"]["contexts"]["items"], {1}, "w/c0")
    assert not c.issues
    assert c.record["events"][0]["participants"] == []
    assert c.record["collectives"][0]["membership_status"] == "unknown"
    assert restore_records(project_records([c.record])) == [c.record]


def test_bad_optional_actor_does_not_remove_independent_collective_event():
    c, s = compiler()
    f = frame()
    f["actors"].append(
        {"actor_type": "person", "mention": {"unit": 1, "text": "absent", "occurrence": None}}
    )
    f["roles"] = [{"actor": 1, "role": "other", "polarity": "affirmed", "units": [1]}]
    c.context(f, s["properties"]["contexts"]["items"], {1}, "w/c0")
    assert len(c.issues) == 2
    assert len(c.record["events"]) == 1
    assert not c.record["roles"]


def test_invalid_event_only_quarantines_its_dependent_statement():
    c, s = compiler()
    f = frame()
    f["events"].append({**copy.deepcopy(f["events"][0]), "participants": []})
    f["statements"] = [
        {
            "subject_kind": "event",
            "subject": 1,
            "predicate": "other",
            "polarity": "negated",
            "units": [1],
            "value": {"kind": "text", "source": {"unit": 1, "text": "42", "occurrence": 0}},
        }
    ]
    c.context(f, s["properties"]["contexts"]["items"], {1}, "w/c0")
    assert len(c.record["events"]) == 1
    assert not c.record["statements"]
    assert len(c.issues) == 2


def test_invalid_speaker_quarantines_context_without_guessing():
    c, s = compiler()
    f = frame()
    f["kind"], f["speakers"] = "party_claim", [99]
    c.context(f, s["properties"]["contexts"]["items"], {1}, "w/c0")
    assert c.issues and not c.record["contexts"] and not c.record["events"]


def test_out_of_scope_evidence_never_becomes_partial_valid_fact():
    c, s = compiler()
    f = frame()
    f["events"][0]["units"] = [0]
    c.context(f, s["properties"]["contexts"]["items"], {1}, "w/c0")
    assert not c.record["events"]
    assert c.record["collectives"] and c.issues


def test_retrieval_available_without_graph_and_expansion_is_evidence_bound():
    rows = index_documents([source()])
    for method in ("bm25", "tfidf", "hybrid"):
        hits = search(rows, "42", method)
        assert hits and hits[0]["evidence"]["quote"] == hits[0]["text"]
        assert graph_expand(rows, hits, [])
    assert not search(rows, "no_match")


def test_space_mapping_preserves_full_original_span_and_hash():
    doc = source()
    # Explicit synthetic metadata source, not a fabricated judicial case.
    field = doc["fields"][0]
    field["text"] = "A\u3000 B"
    from judicial_kg.sources.records import sha256

    field.update(characters=len(field["text"]), sha256=sha256(field["text"].encode()))
    units, _ = batches(doc)
    span, method = locate(doc, units, {"unit": 0, "text": "AB", "occurrence": None}, {0})
    assert span["quote"] == "A\u3000 B"
    assert method == "reversible_horizontal_space_mapping"
    assert span["field_sha256"] == field["sha256"]
    with pytest.raises(ValueError):
        locate(doc, units, {"unit": 0, "text": "AB", "occurrence": None}, {1})


class FakeClient:
    def __init__(self, values):
        self.values = iter(values)

    def call(self, body, timeout):
        value = next(self.values)
        return Response(
            200,
            json.dumps(
                {
                    "model": "qwen3.8-flash",
                    "usage": {"prompt_tokens": 1, "completion_tokens": 1, "total_tokens": 2},
                    "choices": [
                        {"finish_reason": "stop", "message": {"content": json.dumps(value)}}
                    ],
                }
            ).encode(),
            0,
        )


def test_fresh_runner_saves_quarantine_coverage_and_graph(tmp_path):
    doc = source()
    doc["fields"] = doc["fields"][:1]
    units, _ = batches(doc)
    proposal = {
        "contexts": [frame()],
        "coverage": [
            {
                "unit": i,
                "status": "processed" if i == 1 else "no_relevant_content",
                "reason": "fixture",
            }
            for i in range(len(units))
        ],
    }
    verify = {
        f"c0_{k}_0": {"decision": "supported", "units": [1]}
        for k in ("actors", "collectives", "events")
    }
    before = canonical_hash(doc)
    report = run_document(ROOT, doc, tmp_path / "run", FakeClient([proposal, verify]))
    assert report["status"] == "engineering_complete"
    assert report["requests"] == 2
    assert report["lossless_graph_roundtrip"]
    assert report["source_sha256"] == before
    assert report["semantic_precision_recall_f1"] is None
    assert len(json.loads((tmp_path / "run/coverage.json").read_text())) == len(units)


def test_verifier_can_quarantine_event_without_changing_other_indices(tmp_path):
    doc = source()
    doc["fields"] = doc["fields"][:1]
    units, _ = batches(doc)
    proposal = {
        "contexts": [frame()],
        "coverage": [
            {
                "unit": i,
                "status": "processed" if i == 1 else "no_relevant_content",
                "reason": "fixture",
            }
            for i in range(len(units))
        ],
    }
    verify = {
        f"c0_{k}_0": {"decision": "uncertain" if k == "events" else "supported", "units": [1]}
        for k in ("actors", "collectives", "events")
    }
    report = run_document(ROOT, doc, tmp_path / "run", FakeClient([proposal, verify]))
    assert report["status"] == "partial"
    record = json.loads((tmp_path / "run/candidate.json").read_text())
    assert not record["events"] and record["collectives"]


def test_saved_calls_replay_without_network_and_detect_tampering(tmp_path):
    from judicial_kg.current.cli import replay
    from judicial_kg.extraction.fresh_workflow import fingerprint
    from judicial_kg.sources.development import write_json
    from judicial_kg.sources.records import sha256

    doc = source()
    doc["fields"] = doc["fields"][:1]
    units, _ = batches(doc)
    proposal = {
        "contexts": [frame()],
        "coverage": [
            {
                "unit": i,
                "status": "processed" if i == 1 else "no_relevant_content",
                "reason": "fixture",
            }
            for i in range(len(units))
        ],
    }
    decisions = {
        f"c0_{k}_0": {"decision": "supported", "units": [1]}
        for k in ("actors", "collectives", "events")
    }
    dest = tmp_path / "original"
    run_document(ROOT, doc, dest, FakeClient([proposal, decisions]))
    write_json(dest / "config.json", {"window_size": 16, "implementation": fingerprint(ROOT, [])})
    write_json(dest / "outputs.json", {p.name: sha256(p.read_bytes()) for p in dest.iterdir()})
    assert replay(ROOT, dest, tmp_path / "replay")["network_calls"] == 0
    (dest / "candidate.json").write_text("{}")
    with pytest.raises(ValueError, match="Saved artifact changed"):
        replay(ROOT, dest, tmp_path / "tampered")


def test_ambiguous_whitespace_variants_cannot_be_guessed():
    from judicial_kg.sources.records import sha256

    doc = source()
    field = doc["fields"][0]
    field["text"] = "A B A\u3000B"
    field.update(characters=len(field["text"]), sha256=sha256(field["text"].encode()))
    units, _ = batches(doc)
    with pytest.raises(ValueError, match="ambiguous_source_variants"):
        locate(doc, units, {"unit": 0, "text": "AB", "occurrence": 0}, {0})


def test_unresolved_window_does_not_block_later_window_text_or_compilation(tmp_path):
    doc = source()
    units, windows = batches(doc)
    assert len(windows) == 2
    empty = {
        "contexts": [],
        "coverage": [
            {"unit": i, "status": "no_relevant_content", "reason": "fixture"} for i in windows[1]
        ],
    }
    report = run_document(ROOT, doc, tmp_path / "run", FakeClient([{"malformed": True}, empty]))
    assert report["status"] == "partial"
    coverage = json.loads((tmp_path / "run/coverage.json").read_text())
    assert [r["unit"] for r in coverage] == list(range(len(units)))
    assert coverage[-1]["status"] == "no_relevant_content"
    assert len(json.loads((tmp_path / "run/text-index.json").read_text())) == len(units)


def test_partial_membership_is_preserved_without_event_propagation():
    c, s = compiler()
    f = frame()
    f["collectives"][0].update(
        membership_status="partial", members=[{"actor": 0, "units": [1], "polarity": "affirmed"}]
    )
    c.context(f, s["properties"]["contexts"]["items"], {1}, "w/c0")
    assert not c.issues
    assert c.record["collective_memberships"][0]["polarity"] == "affirmed"
    assert c.record["events"][0]["participants"] == []
