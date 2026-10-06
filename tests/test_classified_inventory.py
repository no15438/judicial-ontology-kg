"""Typed discovery ledger coverage tests use metadata only, not legal labels."""

import json

import pytest
from test_anchored import source

from judicial_kg.extraction import classified_inventory as inventory
from judicial_kg.extraction.draft import DraftError


def test_classification_retains_exclusions_indices_and_exact_choices():
    forms = {"forms": ["A", "B", "C"]}
    catalog = {
        "catalog_sha256": "test",
        "proposal_ledger": [{"proposal_id": f"p{i}", "span_ids": [f"s{i}"]} for i in range(3)],
    }
    decisions = {
        "p0": {"kind": "actor_candidate", "span_id": "s0"},
        "p1": {"kind": "legal_instrument_title", "span_id": "s1"},
        "p2": {"kind": "actor_candidate", "span_id": "s2"},
    }
    result, receipt = inventory.apply_classifications(forms, catalog, decisions)
    assert result == {"forms": ["A", "C"]}
    assert receipt["input_count"] == 3 and receipt["legal_title_exclusions"] == 1
    assert [r["compiled_proposal_id"] for r in receipt["ledger"]] == ["p0", None, "p1"]
    assert not receipt["semantic_classification_verified"]
    for key in ["p0", "p1", "p2"]:
        assert key in decisions  # source decision map is unchanged


@pytest.mark.parametrize("change", ["wrong_span", "missing", "unknown", "bad_type"])
def test_type_decision_schema_binds_each_proposal_to_its_own_source(change):
    forms = {"forms": ["A", "B"]}
    catalog = {
        "catalog_sha256": "test",
        "proposal_ledger": [
            {"proposal_id": "p0", "span_ids": ["s0"]},
            {"proposal_id": "p1", "span_ids": ["s1"]},
        ],
    }
    decisions = {
        "p0": {"kind": "actor_candidate", "span_id": "s0"},
        "p1": {"kind": "legal_instrument_title", "span_id": "s1"},
    }
    if change == "wrong_span":
        decisions["p1"]["span_id"] = "s0"
    elif change == "missing":
        decisions.pop("p1")
    elif change == "unknown":
        decisions["p2"] = decisions["p1"]
    else:
        decisions["p1"]["kind"] = "exclude_whatever"
    with pytest.raises(DraftError):
        inventory.apply_classifications(forms, catalog, decisions)


@pytest.mark.parametrize("mode", ["uncertain", "unmatched", "all_excluded", "success"])
def test_classifier_fails_closed_with_complete_saved_ledger(mode):
    forms = {"forms": ["A🙂", "NOT_PRESENT" if mode == "unmatched" else "B"]}
    saved, calls = {}, []

    def infer(messages, fmt, stage):
        calls.append(stage)
        payload = json.loads(messages[-1]["content"])
        decisions = {}
        for row in payload["proposal_ledger"]:
            kind = "actor_candidate"
            if mode == "uncertain" and row["proposal_id"] == "p1":
                kind = "uncertain"
            if mode == "all_excluded":
                kind = "legal_instrument_title"
            decisions[row["proposal_id"]] = {
                "kind": kind,
                "span_id": next(iter(row["span_ids"]), None),
            }
        return json.dumps(decisions)

    if mode == "success":
        assert inventory.classify(source(), forms, infer, saved.__setitem__) == forms
    else:
        with pytest.raises(DraftError):
            inventory.classify(source(), forms, infer, saved.__setitem__)
    receipt = saved["inventory-type-receipt"]
    assert len(receipt["ledger"]) == 2
    assert bool(receipt["unresolved_proposals"]) == (mode in ("uncertain", "unmatched"))
    assert calls == ["inventory-type-000"]
