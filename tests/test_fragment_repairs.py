"""Repair menus contain actual contiguous source fragments, never rejoined text."""

import pytest
from test_anchored import source

from judicial_kg.extraction.draft import DraftError
from judicial_kg.extraction.fragment_repairs import source_fragments


def test_maximal_fragments_split_noncontiguous_text_without_choosing_identity():
    choices = source_fragments(source(), ["u1"], {"forms": ["B42"]}, ["p0"])
    assert choices == {"p0": ["42", "B"]}
    assert "B42" not in choices["p0"]


def test_fragment_menu_cannot_borrow_other_context_or_invent_zero_hit_candidate():
    with pytest.raises(DraftError, match="form_fragment_candidates_missing"):
        source_fragments(source(), ["u0"], {"forms": ["B42"]}, ["p0"])


def test_substrings_of_longer_candidate_are_not_presented_as_extra_forms():
    assert source_fragments(source(), ["u0"], {"forms": ["A🙂X"]}, ["p0"]) == {"p0": ["A🙂"]}
