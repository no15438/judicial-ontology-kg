"""Engineering reporting tests: repaired or unfinished runs cannot masquerade as first pass."""

import pytest

from judicial_kg.extraction.first_pass_report import summarize


def report():
    return {
        "completed": True,
        "run_kind": "local_qwen_development_extraction_not_gold",
        "config": {"version": "qwen-extraction-v8"},
        "expected_documents": 36,
        "selected_documents": 3,
        "requests_submitted": 2,
        "selected_outcomes": [
            {
                "document_id": "metadata:1",
                "status": "accepted_candidate",
                "request_elapsed_seconds": 3,
            },
            {"document_id": "metadata:2", "status": "contract_rejected"},
            {"document_id": "metadata:3", "status": "context_over_budget"},
        ],
        "document_status_counts": {
            "accepted_candidate": 1,
            "contract_rejected": 1,
            "context_over_budget": 1,
            "not_selected": 33,
        },
    }


def test_denominator_includes_preflight_failure_without_request():
    r = summarize(report())
    assert r["first_pass_candidate_rate"] == 1 / 3
    assert r["first_pass_failed"] == 2 and r["requests_submitted"] == 2
    assert r["not_selected"] == 33 and r["post_repair_candidate_accepted"] is None
    assert r["prior_repaired_candidates_counted"] is False


@pytest.mark.parametrize(
    "change",
    [
        lambda r: r.update(completed=False),
        lambda r: r.update(repair_lineage={}),
        lambda r: r["config"].update(version="qwen-extraction-v7"),
        lambda r: r["selected_outcomes"][0].update(status="in_flight"),
        lambda r: r["selected_outcomes"][0].update(document_id="metadata:2"),
        lambda r: r["document_status_counts"].update(not_selected=34),
        lambda r: r.update(requests_submitted=4),
    ],
)
def test_unfinished_repair_and_inconsistent_counts_rejected(change):
    r = report()
    change(r)
    with pytest.raises(ValueError):
        summarize(r)


def test_empty_output_is_separate_from_candidate_success():
    r = report()
    r["selected_outcomes"][0]["status"] = "accepted_empty"
    r["document_status_counts"]["accepted_empty"] = r["document_status_counts"].pop(
        "accepted_candidate"
    )
    s = summarize(r)
    assert s["first_pass_candidate_accepted"] == 0
    assert s["first_pass_empty_accepted"] == 1 and s["first_pass_candidate_rate"] == 0
