"""Explicit plural referents preserve separate actors and exact shared evidence."""

import copy

import pytest
from test_anchored import PROVENANCE
from test_referent_inventory import fixture
from test_scoped import CONTRACT

from judicial_kg.extraction import referent_inventory as protocol
from judicial_kg.extraction.draft import DraftError


def collective_fixture():
    items = fixture(("B", "42"))
    second = copy.deepcopy(items[1][0])
    second["id"] = "second"
    second["mentions"][0]["id"] = "second_mention"
    second["mentions"][0]["evidence"] = {"unit_id": "u2", "text": "C", "occurrence": 0}
    items[1].append(second)
    items[-1]["decisions"]["p0"] = {
        "action": "existing_group",
        "actor_ids": [a["id"] for a in items[1]],
        "span_id": items[3]["proposal_ledger"][0]["span_ids"][0],
    }
    items[-1]["decisions"]["p1"] = {
        "action": "existing",
        "actor_id": "second",
        "span_id": items[3]["proposal_ledger"][1]["span_ids"][0],
    }
    return items


def test_collective_mention_keeps_distinct_identities_and_source_bindings():
    items = collective_fixture()
    before = copy.deepcopy(items[1])
    result, receipt = protocol.compile_decisions(
        *items, CONTRACT, PROVENANCE, allow_collectives=True
    )
    assert len(result) == len(before) == 2
    assert items[1] == before
    assert receipt["new_actors"] == 0
    bindings = [b for b in receipt["bindings"] if b["proposal_id"] == "p0"]
    assert len(bindings) == 2
    assert len({b["mention_id"] for b in bindings}) == 2
    for original, actor in zip(before, result, strict=True):
        assert actor["id"] == original["id"]
        assert actor["actor_type"] == original["actor_type"]
        assert actor["mentions"][: len(original["mentions"])] == original["mentions"]
        assert actor["mentions"][len(original["mentions"])]["evidence"]["text"] == "B"
    assert not receipt["identity_equivalence_verified"]


@pytest.mark.parametrize("change", ["singleton", "duplicate", "unknown", "wrong_span", "v35"])
def test_invalid_collectives_and_old_protocol_reject(change):
    items = collective_fixture()
    decision = items[-1]["decisions"]["p0"]
    if change == "singleton":
        decision["actor_ids"] = decision["actor_ids"][:1]
    elif change == "duplicate":
        decision["actor_ids"] *= 2
    elif change == "unknown":
        decision["actor_ids"][1] = "outside"
    elif change == "wrong_span":
        decision["span_id"] = items[3]["proposal_ledger"][1]["span_ids"][0]
    with pytest.raises(DraftError):
        protocol.compile_decisions(*items, CONTRACT, PROVENANCE, allow_collectives=change != "v35")
