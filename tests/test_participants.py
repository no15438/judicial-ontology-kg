"""Metadata-only role diagnostics preserve context, polarity and candidates."""

import copy

import pytest

from judicial_kg.graph.participants import diagnose_participants


def record(events):
    return {
        "document_id": "metadata:roles",
        "actors": [{"id": "a", "actor_type": "person"}, {"id": "b", "actor_type": "court"}],
        "contexts": [{"id": "c", "kind": "party_claim"}, {"id": "d", "kind": "court_finding"}],
        "events": events,
    }


def event(identifier, kind, participants, context="c", polarity="affirmed"):
    return {
        "id": identifier,
        "event_type": kind,
        "context_id": context,
        "polarity": polarity,
        "participants": [{"actor_id": actor, "role": role} for actor, role in participants],
    }


def test_opposite_roles_are_local_to_each_event_and_preserve_negation():
    r = record(
        [event("e", "loan_agreement", [("a", "lender"), ("a", "borrower")], polarity="negated")]
    )
    before = copy.deepcopy(r)
    d = diagnose_participants(r)
    assert r == before and d["candidate_modified"] is False
    assert d["hint_counts"] == {"same_actor_opposite_roles": 1}
    assert d["hints"][0]["polarity"] == "negated"
    assert d["hints"][0]["context_kind"] == "party_claim"
    assert d["independent_semantic_review"] is False


def test_different_contexts_do_not_complete_each_others_pair():
    r = record(
        [
            event("e", "loan_agreement", [("a", "lender")]),
            event("f", "loan_agreement", [("a", "borrower")], context="d"),
        ]
    )
    d = diagnose_participants(r)
    assert d["hint_counts"] == {"participant_pair_incomplete": 2}
    assert [h["roles"] for h in d["hints"]] == [["borrower"], ["lender"]]
    assert not d["cross_event_identity_inference"]


def test_transfer_loan_roles_are_review_hints_not_direction_repair():
    r = record([event("e", "funds_transfer", [("a", "lender"), ("b", "borrower")])])
    d = diagnose_participants(r)
    assert d["hint_counts"] == {
        "court_in_transaction_event": 1,
        "participant_pair_incomplete": 1,
        "transaction_role_vocabulary_needs_review": 1,
    }
    assert r["events"][0]["participants"][0]["role"] == "lender"


@pytest.mark.parametrize("kind", ["repayment", "guarantee", "demand", "litigation", "other"])
def test_other_event_types_have_no_assumed_direction(kind):
    d = diagnose_participants(record([event("e", kind, [("a", "other")])]))
    assert not d["hints"]


def test_complete_transfer_pair_does_not_assume_loan_roles():
    r = record([event("e", "funds_transfer", [("a", "transferor"), ("b", "transferee")])])
    r["actors"][1]["actor_type"] = "organization"
    assert diagnose_participants(r)["hints"] == []
