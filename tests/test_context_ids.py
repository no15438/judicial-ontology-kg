"""Canonical IDs change bookkeeping only; invalid references remain invalid."""

import copy

import pytest
from test_scoped import fixtures

from judicial_kg.extraction.actor_inventory import canonical_context_ids
from judicial_kg.extraction.draft import DraftError


def test_unicode_context_ids_keep_all_context_contents_and_references():
    h = fixtures()
    contexts = h["contexts"]
    contexts[0]["id"] = "甲 类"
    contexts[1]["parent_context_id"] = "甲 类"
    before = copy.deepcopy(contexts)
    saved = {}
    result = canonical_context_ids(
        {"contexts": contexts}, h["actors"], lambda k, v: saved.update({k: v})
    )
    mapping = saved["context-id-map"]["mapping"]
    for old, new in zip(before, result["contexts"], strict=True):
        assert new["id"] == mapping[old["id"]]
        assert {k: v for k, v in old.items() if k not in {"id", "parent_context_id"}} == {
            k: v for k, v in new.items() if k not in {"id", "parent_context_id"}
        }
    assert result["contexts"][1]["parent_context_id"] == result["contexts"][0]["id"]
    assert contexts == before
    assert not saved["context-id-map"]["semantic_changes"]


@pytest.mark.parametrize("failure", ["duplicate", "unknown_parent"])
def test_bad_ids_are_not_silently_repaired(failure):
    h = fixtures()
    if failure == "duplicate":
        h["contexts"][1]["id"] = h["contexts"][0]["id"]
    else:
        h["contexts"][1]["parent_context_id"] = "missing"
    with pytest.raises(DraftError):
        canonical_context_ids({"contexts": h["contexts"]}, h["actors"], lambda *_: None)
