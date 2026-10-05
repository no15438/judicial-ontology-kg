"""Report first-pass engineering outcomes without crediting repaired candidates."""

from collections import Counter


def summarize(report):
    if (
        report.get("completed") is not True
        or report.get("run_kind") != "local_qwen_development_extraction_not_gold"
        or report.get("config", {}).get("version") != "qwen-extraction-v8"
        or "repair_lineage" in report
    ):
        raise ValueError("First-pass summary requires a completed fresh v8 run")
    outcomes = report["selected_outcomes"]
    selected = report["selected_documents"]
    expected = report["expected_documents"]
    ids = [o["document_id"] for o in outcomes]
    if len(ids) != selected or len(ids) != len(set(ids)) or not 0 < selected <= expected:
        raise ValueError("Invalid first-pass selection denominator")
    counts = Counter(o["status"] for o in outcomes)
    if counts["pending"] or counts["in_flight"] or counts["not_selected"]:
        raise ValueError("First-pass selected outcomes are not final")
    full = Counter(report["document_status_counts"])
    if sum(full.values()) != expected or full["not_selected"] != expected - selected:
        raise ValueError("Invalid full first-pass denominator")
    full.pop("not_selected", None)
    if full != counts or not 0 <= report["requests_submitted"] <= selected:
        raise ValueError("First-pass outcomes/counts disagree")
    accepted = counts["accepted_candidate"]
    empty = counts["accepted_empty"]
    return {
        "run_kind": "fresh_first_pass_engineering_summary_not_gold",
        "expected_documents": expected,
        "selected_documents": selected,
        "requests_submitted": report["requests_submitted"],
        "first_pass_candidate_accepted": accepted,
        "first_pass_empty_accepted": empty,
        "first_pass_failed": selected - accepted - empty,
        "first_pass_status_counts": dict(counts),
        "first_pass_candidate_rate": accepted / selected,
        "not_selected": expected - selected,
        "followup_repair_performed": False,
        "post_repair_candidate_accepted": None,
        "prior_repaired_candidates_counted": False,
        "model_request_seconds": sum(o.get("request_elapsed_seconds", 0) for o in outcomes),
        "gold_available": False,
        "semantic_precision_recall_f1": None,
    }
