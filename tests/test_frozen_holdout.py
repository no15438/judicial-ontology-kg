"""Metadata-only exposure and leakage allocation tests, not case relevance labels."""

import copy
import random

import pytest

from judicial_kg.sources.holdout import freeze
from judicial_kg.sources.records import document


def record(index, text, family=None, **metadata):
    return document(
        "metadata",
        {"record_pointer": f"/{index}"},
        {"metadata": (f"/{index}", text)},
        family_id=family or f"family:{index}",
        allocation="unallocated_not_test",
        **metadata,
    )


def test_new_cohort_excludes_exposed_families_exact_copies_and_quarantine():
    old = record(0, "old metadata text", "exposed")
    pool = [
        record(1, "unseen sibling", "exposed"),
        record(2, "old metadata text"),
        record(3, "quarantined metadata", admission="quarantine"),
        record(4, "new alpha metadata"),
        record(5, "new beta metadata"),
    ]
    before = copy.deepcopy(pool)
    chosen, report = freeze(pool, [old], 2, "fixed-seed")
    assert {d["source"]["record_pointer"] for d in chosen} == {"/4", "/5"}
    assert all(d["allocation"] == "frozen_unexposed_evaluation_once" for d in chosen)
    assert pool == before and report["semantic_precision_recall_f1"] is None


def test_allocation_is_order_independent_and_cannot_select_both_procedural_siblings():
    pool = [record(0, "alpha", "pair"), record(1, "beta", "pair"), record(2, "gamma")]
    first, report = freeze(pool, [], 2, "fixed-seed")
    second, _ = freeze(list(reversed(pool)), [], 2, "fixed-seed")
    assert first == second
    assert len(set(report["families"])) == 2


def test_near_duplicate_content_is_excluded_without_using_model_outcomes():
    rng = random.Random(42)
    text = "".join(rng.choice("abcdefghijklmno") for _ in range(1200))
    old = record(0, text)
    similar = record(1, text + "new suffix")
    new = record(2, "independent metadata content")
    chosen, _ = freeze([similar, new], [old], 1, "fixed-seed")
    assert chosen[0]["document_id"] == new["document_id"]
    with pytest.raises(ValueError, match="Insufficient"):
        freeze([similar], [old], 1, "fixed-seed")


def test_missing_family_and_insufficient_new_sources_cannot_fall_back_to_old_cases():
    old = record(0, "old metadata")
    with pytest.raises(ValueError, match="Insufficient"):
        freeze([old], [old], 1, "fixed-seed")
    del old["family_id"]
    with pytest.raises(ValueError, match="Family"):
        freeze([old], [], 1, "fixed-seed")
