"""Graph integrity fixtures contain only metadata, not invented judicial cases."""

import copy
import json
import shutil
from collections import Counter

import pytest
from test_contracts import ROOT, candidate, source

from judicial_kg.contracts import Contract, candidate_record
from judicial_kg.graph.assembly import node_id, project_records, restore_records
from judicial_kg.graph.diagnostics import diagnose_record, gaps, union_ranges
from judicial_kg.graph.run import assemble_batch, run_assembly
from judicial_kg.sources.records import document, evidence_span


@pytest.fixture
def contract():
    return Contract.load(ROOT)


def batch(docs, records, contract):
    return assemble_batch(docs, [json.dumps(r) for r in records], contract)


def test_graph_roundtrip_preserves_negation_values_context_and_shared_evidence(contract):
    doc = source()
    record = candidate(doc)
    result = batch([doc], [record], contract)
    assert restore_records(result["graph"]) == [record]
    nodes = result["graph"]["nodes"]
    counts = Counter(n["kind"] for n in nodes)
    assert counts == {
        "Document": 1,
        "Actor": 1,
        "Mention": 1,
        "Context": 1,
        "Role": 1,
        "Event": 1,
        "Statement": 1,
        "EvidenceSpan": 3,
    }
    assert result["summary"]["edge_counts"]["supported_by"] == 5
    event = next(n for n in nodes if n["kind"] == "Event")
    assert event["properties"]["polarity"] == "negated"
    actor = next(n for n in nodes if n["kind"] == "Actor")
    assert actor["properties"] == {"id": "a", "actor_type": "unknown"}
    assert not any(
        e["source"] == actor["id"] and e["relation"] == "participant"
        for e in result["graph"]["edges"]
    )
    outcome = result["ledger"]["document_outcomes"][0]
    assert outcome["input_record_sha256"] == outcome["restored_record_sha256"]
    assert outcome["lossless_roundtrip"] is True
    assert result["summary"]["semantic_precision_recall_f1"] is None


def test_claim_finding_and_nested_quotation_are_separate_assertions(contract):
    doc = source()
    record = candidate(doc)
    record["contexts"][0].update(kind="court_finding")
    claim = copy.deepcopy(record["contexts"][0])
    claim.update(id="claim", kind="party_claim", attributed_actor_id="a")
    quoted = copy.deepcopy(record["contexts"][0])
    quoted.update(id="quoted", kind="quoted_material", parent_context_id="c")
    record["contexts"].extend([claim, quoted])
    for identifier, context, polarity in [
        ("claimed", "claim", "affirmed"),
        ("reported", "quoted", "uncertain"),
    ]:
        event = copy.deepcopy(record["events"][0])
        event.update(id=identifier, context_id=context, polarity=polarity)
        record["events"].append(event)
    result = batch([doc], [record], contract)
    assert result["summary"]["node_counts"]["Event"] == 3
    assert restore_records(result["graph"]) == [record]
    assert result["summary"]["edge_counts"]["within_context"] == 1


def test_same_names_and_ids_even_in_same_family_do_not_merge(contract):
    docs = [source(0), source(1)]
    docs[1]["family_id"] = docs[0]["family_id"]
    records = [candidate(doc) for doc in docs]
    first = batch(docs, records, contract)
    second = batch(list(reversed(docs)), list(reversed(records)), contract)
    assert first["graph"] == second["graph"]
    assert first["summary"]["node_counts"]["Actor"] == 2
    assert first["summary"]["node_counts"]["EvidenceSpan"] == 6
    assert first["summary"]["cross_document_identity_merges"] == 0
    assert node_id("a:b", "Actor", "c") != node_id("a", "Actor", "b:c")


def test_array_order_multiple_mentions_and_participant_roles_survive(contract):
    doc = source()
    record = candidate(doc)
    record["mentions"].append(
        {
            "id": "m2",
            "actor_id": "a",
            "kind": "description",
            "evidence": evidence_span(doc, "field", 3, 4),
        }
    )
    record["actors"][0]["mention_ids"] = ["m2", "m"]
    record["events"][0]["participants"] = [
        {"actor_id": "a", "role": "lender"},
        {"actor_id": "a", "role": "borrower"},
    ]
    record["contexts"][0]["evidence"] = [
        evidence_span(doc, "field", 0, 24),
        evidence_span(doc, "field", 3, 4),
    ]
    result = batch([doc], [record], contract)
    assert restore_records(result["graph"]) == [record]


@pytest.mark.parametrize(
    "change",
    [
        "dangling",
        "cross_document",
        "duplicate",
        "index",
        "boolean_index",
        "unused",
        "relation",
        "status",
        "pointer",
    ],
)
def test_corrupted_graph_is_rejected(change):
    records = [candidate(source(0)), candidate(source(1))]
    graph = project_records(records)
    if change == "dangling":
        graph["edges"][0]["target"] = "missing"
    elif change == "cross_document":
        edge = graph["edges"][0]
        nodes = {n["id"]: n for n in graph["nodes"]}
        edge["target"] = next(
            n["id"]
            for n in graph["nodes"]
            if n["document_id"] != nodes[edge["source"]]["document_id"]
        )
    elif change == "duplicate":
        graph["edges"].append(copy.deepcopy(graph["edges"][0]))
    elif change == "index":
        graph["edges"][0]["index"] += 3
    elif change == "boolean_index":
        edge = next(e for e in graph["edges"] if e["index"] == 0)
        edge["index"] = False
    elif change == "unused":
        node = copy.deepcopy(graph["nodes"][0])
        node["id"] = "extra"
        graph["nodes"].append(node)
    elif change == "relation":
        graph["edges"][0]["relation"] = "inferred_fact"
    elif change == "status":
        graph["reference_status"] = "gold"
    else:
        graph["nodes"][0]["record_pointer"] = "/wrong/0"
    with pytest.raises(ValueError):
        restore_records(graph)


def test_duplicate_rejected_missing_and_empty_documents_remain_in_ledger(contract):
    docs = [source(i) for i in range(5)]
    invalid = candidate(docs[1])
    invalid["mentions"][0]["evidence"]["quote"] = "not-source"
    empty = candidate_record(docs[3], candidate(docs[3])["provenance"], [])
    result = batch(
        docs, [candidate(docs[0]), invalid, empty, candidate(docs[4]), candidate(docs[4])], contract
    )
    assert result["summary"]["document_status_counts"] == {
        "assembled_candidate": 1,
        "assembled_empty": 1,
        "missing": 1,
        "rejected": 2,
    }
    assert len(result["ledger"]["document_outcomes"]) == 5
    assert result["summary"]["validation"]["attempts_rejected"] == 3
    assert result["summary"]["node_counts"]["Document"] == 2


def test_assembly_failure_is_accounted_and_no_partial_nodes_leak(contract, monkeypatch):
    from judicial_kg.graph import run

    doc = source()
    record = candidate(doc)

    def lossy(records):
        altered = copy.deepcopy(records)
        altered[0]["statements"] = []
        return project_records(altered)

    monkeypatch.setattr(run, "project_records", lossy)
    result = batch([doc], [record], contract)
    assert result["summary"]["validation"]["documents_accepted_candidate"] == 1
    assert result["summary"]["documents_assembly_failed"] == 1
    assert not result["graph"]["nodes"]
    assert result["ledger"]["document_outcomes"][0]["issues"] == [
        {"code": "assembly_integrity_failed", "path": "/"}
    ]


def test_source_tampering_and_blind_test_manifest_fail_before_assembly(contract):
    doc = source()
    record = candidate(doc)
    doc["allocation"] = "test"
    with pytest.raises(ValueError, match="exposed development"):
        batch([doc], [record], contract)
    doc = source()
    doc["fields"][0]["text"] += "tampered"
    with pytest.raises(ValueError, match="Changed source"):
        batch([doc], [record], contract)


def test_coverage_merges_overlaps_and_does_not_call_processed_complete():
    doc = source()
    record = candidate(doc)
    record["contexts"][0]["evidence"] = [
        evidence_span(doc, "field", 0, 2),
        evidence_span(doc, "field", 1, 4),
    ]
    diagnosis = diagnose_record(record, doc)
    field = diagnosis["fields"][0]
    assert field["reported_processed"] is True
    assert field["context_evidence_characters"] == 4
    assert field["outside_context_evidence_ranges"] == [[4, doc["fields"][0]["characters"]]]
    assert diagnosis["independent_semantic_review"] is False
    assert union_ranges([(3, 7), (0, 3), (1, 2)]) == [[0, 7]]
    assert gaps([], 0) == []


def test_literal_name_and_attribution_hints_do_not_rewrite_candidates(contract):
    # Isolated diagnostic tokens, not a fabricated legal document or gold case.
    text = "AB|AB|共同辩称|证言|AB"
    doc = document(
        "metadata",
        {"record_pointer": "/hints"},
        {"field": ("/hints/text", text)},
        family_id="metadata",
        allocation="development_exposed",
    )
    record = candidate_record(doc, candidate(source())["provenance"], ["field"])
    record["actors"] = [{"id": "a", "actor_type": "unknown", "mention_ids": ["m"]}]
    record["mentions"] = [
        {"id": "m", "actor_id": "a", "kind": "name", "evidence": evidence_span(doc, "field", 0, 2)}
    ]
    record["contexts"] = [
        {
            "id": "c",
            "kind": "party_defense",
            "attributed_actor_id": "a",
            "parent_context_id": None,
            "evidence": [evidence_span(doc, "field", 0, len(text))],
        }
    ]
    finding = copy.deepcopy(record["contexts"][0])
    finding.update(id="finding", kind="court_finding", attributed_actor_id=None)
    record["contexts"].append(finding)
    original = copy.deepcopy(record)
    result = batch([doc], [record], contract)
    assert record == original
    assert restore_records(result["graph"]) == [original]
    assert (
        result["summary"]["assembled_documents_diagnostics"][
            "literal_name_occurrences_without_mention"
        ]
        == 2
    )
    assert result["summary"]["diagnostic_hint_counts"] == {
        "joint_attribution_needs_review": 1,
        "reported_material_in_finding_context": 1,
    }


def test_cli_exports_locally_and_preserves_prior_outputs(tmp_path, monkeypatch, capsys):
    from judicial_kg.cli import main
    from judicial_kg.extraction.transport import SparkClient

    def forbidden(*args, **kwargs):
        pytest.fail("Offline graph assembly cannot call model service")

    monkeypatch.setattr(SparkClient, "call", forbidden)
    documents = tmp_path / "documents.json"
    candidates = tmp_path / "candidates.jsonl"
    documents.write_text(json.dumps([source(0), source(1)]))
    candidates.write_text(json.dumps(candidate(source(0))) + "\n" + "broken\n")
    output = ROOT / "artifacts" / tmp_path.name
    monkeypatch.setattr(
        "sys.argv",
        [
            "judicial-kg",
            "assemble-graph",
            "--root",
            str(ROOT),
            "--documents",
            str(documents),
            "--candidates",
            str(candidates),
            "--output",
            str(output),
        ],
    )
    try:
        assert main() == 1  # Missing input and malformed attempt cannot disappear.
        captured = capsys.readouterr()
        report = json.loads(captured.out)
        assert "A🙂" not in captured.out
        assert report["documents_assembled"] == 1
        assert report["validation"]["documents_missing"] == 1
        assert report["validation"]["attempts_rejected"] == 1
        assert report["requests_submitted"] == 0
        graph = json.loads((output / "graph.json").read_text())
        assert restore_records(graph) == [candidate(source(0))]
        before = {p.name: p.read_bytes() for p in output.iterdir()}
        assert main() == 1
        assert "new output directory" in capsys.readouterr().err
        assert before == {p.name: p.read_bytes() for p in output.iterdir()}
        with pytest.raises(ValueError, match="must stay"):
            run_assembly(ROOT, [documents], candidates, ROOT / "research/not-allowed")
    finally:
        shutil.rmtree(output, ignore_errors=True)
