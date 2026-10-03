"""Metadata-only fixtures exercise invariants; none are legal case annotations."""

import copy
import json
from pathlib import Path

import pytest

from judicial_kg.contracts import Contract, candidate_record
from judicial_kg.sources.records import document, evidence_span
from judicial_kg.validation import validate_record
from judicial_kg.validation_run import strict_json, validate_batch

ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture
def contract():
    return Contract.load(ROOT)


def source(index=0):
    return document(
        "metadata",
        {"record_pointer": f"/{index}"},
        {
            "field": (f"/{index}/field", "A🙂|B|100|2020-01-02|C|META"),
            "extra": (f"/{index}/extra", ""),
        },
        family_id=f"metadata-family:{index}",
        allocation="development_exposed",
    )


def candidate(doc):
    span = lambda start, end: evidence_span(doc, "field", start, end)  # noqa: E731
    record = candidate_record(
        doc,
        {"method": "rule", "producer": "metadata-fixture", "version": "1", "run_id": "unit"},
        ["field"],
    )
    record["actors"] = [{"id": "a", "actor_type": "unknown", "mention_ids": ["m"]}]
    record["mentions"] = [
        {"id": "m", "actor_id": "a", "kind": "description", "evidence": span(0, 2)}
    ]
    record["contexts"] = [
        {
            "id": "c",
            "kind": "unknown",
            "attributed_actor_id": None,
            "parent_context_id": None,
            "evidence": [span(0, 24)],
        }
    ]
    record["roles"] = [
        {
            "id": "r",
            "actor_id": "a",
            "role": "other",
            "context_id": "c",
            "polarity": "uncertain",
            "evidence": [span(0, 2)],
        }
    ]
    record["events"] = [
        {
            "id": "e",
            "event_type": "other",
            "participants": [{"actor_id": "a", "role": "other"}],
            "context_id": "c",
            "polarity": "negated",
            "evidence": [span(0, 24)],
        }
    ]
    record["statements"] = [
        {
            "id": "s",
            "subject_id": "e",
            "predicate": "principal_amount",
            "context_id": "c",
            "polarity": "negated",
            "evidence": [span(5, 8)],
            "value": {"kind": "money", "raw": "100", "normalized": "100.00", "unit": "CNY"},
        }
    ]
    return record


def codes(record, doc, contract):
    return {i["code"] for i in validate_record(record, doc, contract)}


def test_versioned_contract_accepts_traceable_candidate_with_negation(contract):
    doc = source()
    record = candidate(doc)
    assert validate_record(record, doc, contract) == []
    assert record["events"][0]["polarity"] == "negated"
    assert contract.versions()["ontology_version"] == "0.1.0"


@pytest.mark.parametrize(
    "mutation,expected",
    [
        (lambda r: r.update(reference_status="gold"), "schema:const"),
        (lambda r: r.update(unknown_fact=True), "schema:additionalProperties"),
        (lambda r: r["roles"][0].update(role="person"), "schema:enum"),
        (lambda r: r["actors"][0].update(actor_type="borrower"), "schema:enum"),
        (lambda r: r["events"][0].update(polarity="happened"), "schema:enum"),
        (lambda r: r["provenance"].update(method="human_gold"), "schema:enum"),
        (lambda r: r["roles"][0].update(evidence=[]), "schema:minItems"),
        (lambda r: r["mentions"][0].update(actor_id="missing"), "unresolved_reference"),
        (lambda r: r["roles"][0].update(id="a"), "duplicate_id"),
        (lambda r: r["actors"][0].update(mention_ids=["absent"]), "mention_actor_disagreement"),
        (lambda r: r["contexts"][0].update(kind="party_claim"), "party_attribution_required"),
        (
            lambda r: r["contexts"][0].update(kind="court_finding", attributed_actor_id="a"),
            "court_attribution_type",
        ),
        (lambda r: r["contexts"][0].update(parent_context_id="c"), "context_cycle"),
        (lambda r: r["contexts"][0].update(parent_context_id="absent"), "unresolved_reference"),
        (lambda r: r.update(family_id="other"), "source_identity_mismatch"),
        (lambda r: r["coverage"].update(unprocessed_fields=[]), "field_coverage_not_partition"),
        (
            lambda r: r["coverage"].update(
                processed_fields=["extra"], unprocessed_fields=["field"]
            ),
            "evidence_in_unprocessed_field",
        ),
        (
            lambda r: r["statements"][0]["value"].update(raw="not-in-field"),
            "value_raw_not_in_evidence",
        ),
        (lambda r: r["statements"][0]["value"].update(normalized=100.0), "schema:anyOf"),
        (lambda r: r["statements"][0]["value"].update(normalized="1e2"), "decimal_string_required"),
        (lambda r: r["statements"][0]["value"].update(kind="date"), "predicate_value_kind"),
        (lambda r: r["statements"][0]["value"].update(unit="USD"), "unsupported_unit"),
    ],
)
def test_invalid_candidates_rejected(contract, mutation, expected):
    doc = source()
    record = candidate(doc)
    mutation(record)
    assert expected in codes(record, doc, contract)


@pytest.mark.parametrize(
    "field,value",
    [
        ("quote", "fabricated"),
        ("document_id", "other"),
        ("source_pointer", "/other"),
        ("field_sha256", "0" * 64),
        ("start", 1),
        ("end", 3),
        ("end", True),
    ],
)
def test_evidence_cannot_be_relocated_or_rewritten(contract, field, value):
    doc = source()
    record = candidate(doc)
    record["mentions"][0]["evidence"][field] = value
    assert validate_record(record, doc, contract)


def test_context_containment_and_event_attribution(contract):
    doc = source()
    record = candidate(doc)
    claim = copy.deepcopy(record["contexts"][0])
    claim.update(id="claim", kind="party_claim", attributed_actor_id="a")
    claim["evidence"] = [evidence_span(doc, "field", 0, 2)]
    record["contexts"].append(claim)
    record["events"][0]["context_id"] = "claim"
    assert {"evidence_outside_context", "event_statement_context_mismatch"} <= codes(
        record, doc, contract
    )
    record["contexts"][0]["parent_context_id"] = "claim"
    assert "context_outside_parent" in codes(record, doc, contract)


def test_date_unknown_stays_unknown_and_impossible_date_is_rejected(contract):
    doc = source()
    record = candidate(doc)
    statement = record["statements"][0]
    statement.update(predicate="event_date", evidence=[evidence_span(doc, "field", 9, 19)])
    statement["value"] = {
        "kind": "date",
        "raw": "2020-01-02",
        "normalized": "2020-01-02",
        "unit": None,
    }
    assert not codes(record, doc, contract)
    statement["value"]["normalized"] = "2020-02-30"
    assert "invalid_iso_date" in codes(record, doc, contract)
    statement["value"].update(raw="2020", normalized=None)
    assert not codes(record, doc, contract)
    statement["value"]["normalized"] = "2020-01-01"
    assert "date_normalization_not_supported_by_raw" in codes(record, doc, contract)


def test_source_corruption_even_in_unprocessed_fields_fails(contract):
    doc = source()
    record = candidate(doc)
    doc["fields"][1]["text"] = "changed"
    assert "invalid_source" in codes(record, doc, contract)
    doc = source()
    doc["fields"][0]["text"] = 123
    assert "invalid_source" in codes(record, doc, contract)
    with pytest.raises(ValueError, match="source field structure"):
        validate_batch([doc], [], contract)


def test_same_local_ids_in_different_documents_do_not_merge(contract):
    docs = [source(0), source(1)]
    result = validate_batch(docs, [json.dumps(candidate(d)) for d in docs], contract)
    assert result["summary"]["documents_accepted_candidate"] == 2
    assert len(result["accepted_records"]) == 2
    assert (
        result["accepted_records"][0]["document_id"] != result["accepted_records"][1]["document_id"]
    )
    assert result["summary"]["semantic_precision_recall_f1"] is None


def test_missing_rejected_duplicate_and_malformed_attempts_remain_visible(contract):
    docs = [source(i) for i in range(3)]
    valid = candidate(docs[0])
    invalid = candidate(docs[1])
    invalid["reference_status"] = "gold"
    lines = [json.dumps(valid), json.dumps(valid), json.dumps(invalid), "broken", "[]"]
    result = validate_batch(docs, lines, contract)
    summary = result["summary"]
    assert summary["expected_documents"] == 3
    assert summary["attempts_total"] == summary["attempts_rejected"] == 5
    assert summary["documents_rejected"] == 2
    assert summary["documents_missing"] == 1
    assert not result["accepted_records"]
    assert summary["issue_counts"]["duplicate_document_attempt"] == 2


def test_empty_output_is_explicitly_not_semantic_completeness(contract):
    doc = source()
    record = candidate_record(
        doc, {"method": "rule", "producer": "meta", "version": "1", "run_id": "x"}, []
    )
    result = validate_batch([doc], [json.dumps(record)], contract)
    assert result["summary"]["documents_accepted_empty"] == 1
    assert result["summary"]["semantic_completeness"] == "not_assessed"
    assert result["summary"]["gold_available"] is False


@pytest.mark.parametrize("raw", ['{"a":1,"a":2}', '{"a":NaN}', '{"a":Infinity}'])
def test_ambiguous_json_is_rejected(raw):
    with pytest.raises(ValueError):
        strict_json(raw)


def test_manifest_cannot_contain_blind_test_or_duplicate_documents(contract):
    doc = source()
    with pytest.raises(ValueError, match="Duplicate expected"):
        validate_batch([doc, doc], [], contract)
    doc["allocation"] = "test"
    with pytest.raises(ValueError, match="exposed development"):
        validate_batch([doc], [], contract)


def test_schema_vocabulary_drift_fails_closed(tmp_path):
    for directory in ("ontology", "schemas"):
        (tmp_path / directory).mkdir()
        for source_file in (ROOT / directory).glob("*.json"):
            (tmp_path / directory / source_file.name).write_bytes(source_file.read_bytes())
    path = tmp_path / "ontology/private-lending-v0.1.json"
    value = json.loads(path.read_text())
    value["roles"].append("unreviewed_role")
    path.write_text(json.dumps(value))
    with pytest.raises(ValueError, match="Schema and ontology differ"):
        Contract.load(tmp_path)


def test_cli_keeps_rejections_and_raw_text_out_of_console(tmp_path, monkeypatch, capsys):
    from judicial_kg.cli import main

    document_path = tmp_path / "source.json"
    candidate_path = tmp_path / "candidates.jsonl"
    document_path.write_text(json.dumps([source(0), source(1)]))
    candidate_path.write_text(json.dumps(candidate(source(0))) + "\n" + "broken\n")
    output = ROOT / "artifacts" / tmp_path.name
    # Use an ignored repository output so the real CLI path guard is exercised.
    monkeypatch.setattr(
        "sys.argv",
        [
            "judicial-kg",
            "validate-extractions",
            "--root",
            str(ROOT),
            "--documents",
            str(document_path),
            "--candidates",
            str(candidate_path),
            "--output",
            str(output),
        ],
    )
    try:
        assert main() == 1
        captured = capsys.readouterr()
        report = json.loads(captured.out)
        assert report["documents_missing"] == 1
        assert report["attempts_rejected"] == 1
        assert report["source_fields_verified"] == 4
        assert report["accepted_evidence_spans_replayed"] == 5
        assert "A🙂" not in captured.out
        ledger = json.loads((output / "ledger.json").read_text())
        assert len(ledger["document_outcomes"]) == len(ledger["attempts"]) == 2
    finally:
        import shutil

        shutil.rmtree(output, ignore_errors=True)


def test_run_cannot_overwrite_input(tmp_path):
    from judicial_kg.validation_run import run_validation

    output = tmp_path / "artifacts"
    output.mkdir()
    path = output / "accepted.json"
    path.write_text("[]")
    with pytest.raises(ValueError, match="overwrite an input"):
        run_validation(tmp_path, [path], tmp_path / "candidate.jsonl", output)
    assert path.read_text() == "[]"
