"""A source description can anchor an unnamed actor without inventing a name."""

import pytest
from test_actor_inventory import catalog, selection
from test_anchored import PROVENANCE, source
from test_scoped import CONTRACT

from judicial_kg.extraction import actor_inventory
from judicial_kg.extraction.draft import DraftError


def test_description_anchor_keeps_literal_kind_without_claiming_identity_verified():
    c = catalog()
    selected = selection(c)
    selected["actors"][0]["anchor_kind"] = "description"
    result = actor_inventory.compile_inventory(
        source(), c, selected, CONTRACT, PROVENANCE, descriptive=True
    )
    actor = result["actors"][0]
    assert actor["id"] == "a"
    assert actor["mentions"][0]["kind"] == "description"
    assert actor["mentions"][0]["evidence"]["text"] == "A🙂"
    assert not result["semantic_identity_verified"]
    assert not result["complete_actor_coverage"]
    assert set(actor) == {"id", "actor_type", "mentions"}


@pytest.mark.parametrize("kind", ["pronoun", "invented", None])
def test_description_support_does_not_allow_unvalidated_anchor_kinds(kind):
    c = catalog()
    selected = selection(c)
    if kind is not None:
        selected["actors"][0]["anchor_kind"] = kind
    with pytest.raises(DraftError):
        actor_inventory.compile_inventory(
            source(), c, selected, CONTRACT, PROVENANCE, descriptive=True
        )
