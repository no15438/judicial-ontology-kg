"""V32 regression fixtures are metadata, not invented legal cases or model evidence."""

import copy
import json

import pytest
from test_grouped_mentions import fixture as mentions_fixture
from test_owner_first import fixture as owner_fixture

from judicial_kg.extraction import bounded_workflow as workflow
from judicial_kg.extraction import evidence_first, grouped_mentions, span_catalog
from judicial_kg.extraction.context_first import check_schema
from judicial_kg.extraction.draft import DraftError
from judicial_kg.sources.records import document, verify_evidence


@pytest.mark.parametrize("text", ["2013.5.30", "2024/2/29", "２０２４．２．２９", "2026-10-06"])
def test_numeric_dates_are_calendar_checked(text):
    assert "date" in workflow.literal_kinds(text)


@pytest.mark.parametrize("text", ["2023.2.29", "2024.13.1", "2024.2/29", "12024.2.29", "42"])
def test_invalid_dates_are_not_date_options(text):
    assert "date" not in workflow.literal_kinds(text)


def date_fixture():
    doc = document(
        "metadata",
        {"record_pointer": "/0"},
        {"field": ("/0/field", "2013.5.30号，２０２４．２．２９；2023.2.29，12024.2.29。")},
        family_id="metadata:dates",
        allocation="development_exposed",
    )
    from judicial_kg.extraction.anchored import source_units

    context = {"assertion_units": [u["id"] for u in source_units(doc)]}
    return doc, context


def test_date_catalog_preserves_exact_source_and_rejects_tampering():
    doc, context = date_fixture()
    catalog = workflow.build_evidence_catalog(doc, context)
    entries = {e["selector"]["text"]: e for e in catalog["entries"]}
    assert "2013.5.30" in entries and "２０２４．２．２９" in entries
    assert "2023.2.29" not in entries and "2024.2.29" not in entries
    for entry in catalog["entries"]:
        verify_evidence(doc, entry["evidence"])
    facts = {"events": [], "statements": []}
    assert (
        evidence_first.compile_facts(
            doc, context, catalog, facts, catalog_builder=workflow.build_evidence_catalog
        )
        == facts
    )
    changed = copy.deepcopy(catalog)
    changed["entries"].pop()
    with pytest.raises(DraftError, match="fact_evidence_catalog_changed"):
        evidence_first.compile_facts(
            doc, context, changed, facts, catalog_builder=workflow.build_evidence_catalog
        )


def test_proposal_positions_come_only_from_own_ledger_and_single_binding_table():
    doc, catalog, grouped, old = mentions_fixture()
    catalog["proposal_ledger"].append({"proposal_id": "p1", "span_ids": ["different"]})
    schema = workflow.decision_schema(grouped, catalog)
    raw = {**old, "proposal_decisions": {"p0": "select", "p1": "uncertain"}}
    check_schema(raw, schema)
    expanded = workflow.expand_decisions(raw, catalog)
    assert expanded["proposal_decisions"][0]["span_ids"] == list(raw["mentions"])
    assert expanded["proposal_decisions"][1]["span_ids"] == []
    raw["proposal_decisions"]["p1"] = "select"
    with pytest.raises(DraftError, match="selected_proposal_has_no_mention_binding"):
        workflow.expand_decisions(raw, catalog)
    # Preserve valid collective identities and compile under the original strict validator.
    catalog["proposal_ledger"].pop()
    expanded["proposal_decisions"].pop()
    compiled = span_catalog.compile_selection(
        doc, ["u0"], catalog, ["a", "b"], grouped_mentions.expand_bindings(expanded)
    )
    assert len(compiled["mentions"]) == 2


@pytest.mark.parametrize("change", ["missing", "unknown", "old_array"])
def test_decision_coverage_cannot_be_silently_lost(change):
    _, catalog, grouped, old = mentions_fixture()
    schema = workflow.decision_schema(grouped, catalog)
    raw = {**old, "proposal_decisions": {"p0": "select"}}
    if change == "missing":
        raw["proposal_decisions"].pop("p0")
    elif change == "unknown":
        raw["proposal_decisions"]["other"] = "select"
    else:
        raw["proposal_decisions"] = old["proposal_decisions"]
    with pytest.raises(DraftError):
        check_schema(raw, schema)


def batch_fixture():
    doc, context, catalog, facts, schema, result, _ = owner_fixture()
    view = [{"span_id": f"v{i}", **e["selector"]} for i, e in enumerate(catalog["entries"])]
    payload = {
        "frozen_events_and_roles": facts,
        "evidence_catalog": view,
        "owner_choices": {
            e["id"]: [v["span_id"] for v in view if v["unit_id"] in e["evidence_units"]]
            for e in facts["events"]
        },
    }
    return doc, schema, result, payload


def test_owner_batches_keep_all_events_and_compile_single_result():
    doc, schema, result, payload = batch_fixture()
    original = copy.deepcopy(payload)
    calls, saved = [], {}

    def infer(messages, fmt, stage):
        data = json.loads(messages[-1]["content"])
        owners = [e["id"] for e in data["frozen_events_and_roles"]["events"]]
        assert len(owners) <= 1
        assert messages[0]["content"].count("JSON Schema:\n") == 1
        prompt_schema = json.loads(messages[0]["content"].split("JSON Schema:\n", 1)[1])
        assert workflow.response_format(doc, prompt_schema) == fmt
        calls.append(stage)
        value = {
            "statements": [],
            "event_statements": {key: result["event_statements"][key] for key in owners},
        }
        check_schema(value, fmt["schema"])
        return json.dumps(value)

    raw = workflow.batch_properties(
        doc,
        [{"content": json.dumps(payload)}],
        {"schema": schema},
        "body-000-properties",
        infer,
        saved.__setitem__,
    )
    assert json.loads(raw) == result and payload == original
    assert len(calls) == 3
    assert saved["body-000-properties-owner-ledger"]["complete_owner_coverage"]


def test_missing_batch_owner_or_cross_owner_source_rejects_whole_result():
    doc, schema, result, payload = batch_fixture()

    def wrong(messages, fmt, stage):
        return json.dumps({"statements": [], "event_statements": {}})

    saved = {}
    with pytest.raises(DraftError):
        workflow.batch_properties(
            doc,
            [{"content": json.dumps(payload)}],
            {"schema": schema},
            "body-000-properties",
            wrong,
            saved.__setitem__,
        )
    assert "body-000-properties-owner-ledger" not in saved


def test_batch_detects_changed_owner_ledger_before_any_inference():
    _, schema, _, payload = batch_fixture()
    payload["frozen_events_and_roles"]["events"].pop()
    with pytest.raises(DraftError, match="property_owner_ledger_changed"):
        list(workflow.owner_batches(schema, payload))
