"""Metadata-only staging checks, never legal gold."""

import copy

import pytest
from test_selector_repair import decision, example

from judicial_kg.extraction.anchored import GroundingError
from judicial_kg.extraction.draft import DraftError
from judicial_kg.extraction.selector_repair import apply_choices, stage_choices


def test_resolved_positions_survive_pending_decisions_without_acceptance():
    _, draft, plan, choices = example()
    original = copy.deepcopy(draft)
    choices["selector_choices"][1] = decision(plan["selector_items"][1], "uncertain")
    patched, counts, pending = stage_choices(draft, plan, choices)
    assert draft == original
    assert patched["mentions"][0]["evidence"]["occurrence"] == 0
    assert patched["mentions"][1] == original["mentions"][1]
    assert counts == {"locate": 1, "uncertain": 1}
    assert len(pending) == 1
    assert patched["contexts"] == original["contexts"]
    assert patched["actors"] == original["actors"]
    with pytest.raises(GroundingError):
        apply_choices(draft, plan, choices)


@pytest.mark.parametrize("bad", ["choice_id", "local_text", "mention_kind"])
def test_uncertainty_is_not_a_patch_or_a_deletion(bad):
    _, draft, plan, choices = example()
    choices["selector_choices"][1] = decision(plan["selector_items"][1], "uncertain", **{bad: "x"})
    with pytest.raises(DraftError):
        stage_choices(draft, plan, choices)


def test_all_resolved_staging_matches_existing_application():
    _, draft, plan, choices = example()
    patched, counts, pending = stage_choices(draft, plan, choices)
    assert pending == []
    assert (patched, counts) == apply_choices(draft, plan, choices)
