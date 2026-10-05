"""Read-only event participation hints; no legal inference or candidate repair."""

from collections import Counter, defaultdict

VERSION = "event-participant-diagnostics-v1"
PAIRS = {
    "loan_agreement": ("lender", "borrower"),
    "funds_transfer": ("transferor", "transferee"),
}


def diagnose_participants(record):
    """Inspect a source-validated record without merging events or contexts.

    A hint means review is needed, not that the text is wrong or incomplete.
    Repayment and other event types have no assumed direction here.
    """
    contexts = {c["id"]: c for c in record["contexts"]}
    actors = {a["id"]: a for a in record["actors"]}
    hints, events = [], []
    for event in record["events"]:
        context = contexts[event["context_id"]]
        by_actor = defaultdict(set)
        for participant in event["participants"]:
            actor = actors[participant["actor_id"]]
            by_actor[actor["id"]].add(participant["role"])
        roles = {p["role"] for p in event["participants"]}
        base = {
            "event_id": event["id"],
            "event_type": event["event_type"],
            "context_id": event["context_id"],
            "context_kind": context["kind"],
            "polarity": event["polarity"],
        }
        pair = PAIRS.get(event["event_type"])
        if pair:
            missing = sorted(set(pair) - roles)
            if missing:
                hints.append({**base, "code": "participant_pair_incomplete", "roles": missing})
            for actor_id, actor_roles in sorted(by_actor.items()):
                if set(pair) <= actor_roles:
                    hints.append(
                        {**base, "code": "same_actor_opposite_roles", "actor_id": actor_id}
                    )
                if actors[actor_id]["actor_type"] == "court":
                    hints.append(
                        {**base, "code": "court_in_transaction_event", "actor_id": actor_id}
                    )
            alternative = PAIRS[
                "funds_transfer" if event["event_type"] == "loan_agreement" else "loan_agreement"
            ]
            if set(alternative) & roles:
                hints.append(
                    {
                        **base,
                        "code": "transaction_role_vocabulary_needs_review",
                        "roles": sorted(set(alternative) & roles),
                    }
                )
        events.append(
            {
                **base,
                "participant_role_counts": dict(Counter(p["role"] for p in event["participants"])),
            }
        )
    return {
        "document_id": record["document_id"],
        "diagnostic_version": VERSION,
        "events": events,
        "hints": hints,
        "hint_counts": dict(sorted(Counter(h["code"] for h in hints).items())),
        "interpretation": "structural_role_review_hints_not_semantic_errors_or_recall",
        "candidate_modified": False,
        "cross_event_identity_inference": False,
        "independent_semantic_review": False,
    }
