"""Predeclared engineering acceptance, separate from semantic completeness."""


def assess(record, issues, coverage, verification_audit, expected_units):
    problems = []
    if sorted(r["unit"] for r in coverage) != list(range(expected_units)):
        problems.append("coverage_partition")
    if any(r["status"] == "failed" for r in coverage):
        problems.append("failed_source_units")
    if any(r["status"] == "protocol_unresolved" for r in verification_audit):
        problems.append("verification_protocol_unresolved")
    safe_quarantine = {"verifier_rejected", "dependency_quarantined"}
    blocking = [
        r
        for r in issues
        if r.get("status") != "model_unresolved" and r.get("code") not in safe_quarantine
    ]
    if blocking:
        problems.append("unresolved_engineering_issues")
    if not record["events"] or not record["statements"]:
        problems.append("empty_event_or_statement_channel")
    return {
        "engineering_pipeline_passed": not problems,
        "blocking_reasons": problems,
        "blocking_issue_count": len(blocking),
        "semantic_completeness_verified": False,
        "explicit_quarantine_count": len(issues) - len(blocking),
    }
