"""Closed referent choices and immutable identities tested with metadata-only sources."""

import copy
import json

import pytest
from test_anchored import PROVENANCE, source
from test_augmented_inventory import named
from test_scoped import CONTRACT

from judicial_kg.extraction import actor_inventory, compact_inventory, space_inventory
from judicial_kg.extraction import referent_inventory as protocol
from judicial_kg.extraction.anchored import source_units
from judicial_kg.extraction.draft import DraftError


def fixture(forms=("B", "C")):
    doc, actors = source(), named()
    catalog = space_inventory.build_catalog(
        doc,
        [u["id"] for u in source_units(doc)],
        [],
        [{"id": f"p{i}", "text": text} for i, text in enumerate(forms)],
    )
    _, view, aliases = compact_inventory.compact_view(catalog, {})
    raw = {
        "catalog_sha256": view["catalog_sha256"],
        "decisions": {
            row["proposal_id"]: {
                "action": "new",
                "entity_slot": "n0",
                "actor_type": "person",
                "span_id": row["span_ids"][0],
            }
            for row in view["proposal_ledger"]
        },
    }
    return doc, actors, catalog, view, aliases, raw


def compile_fixture(items):
    return protocol.compile_decisions(*items, CONTRACT, PROVENANCE)


def test_new_slots_are_compiled_once_and_keep_every_source_binding():
    items = fixture()
    before = copy.deepcopy(items[1])
    result, receipt = compile_fixture(items)
    assert result[:-1] == before == items[1]
    assert result[-1]["id"].startswith("anon_")
    assert [m["evidence"]["text"] for m in result[-1]["mentions"]] == ["B", "C"]
    assert receipt["new_actors"] == 1 and len(receipt["bindings"]) == 2
    assert not receipt["identity_equivalence_verified"]


def test_existing_reference_appends_mention_without_changing_identity():
    items = fixture(("B",))
    items[-1]["decisions"]["p0"] = {
        "action": "existing",
        "actor_id": items[1][0]["id"],
        "span_id": items[3]["proposal_ledger"][0]["span_ids"][0],
    }
    before = copy.deepcopy(items[1])
    result, receipt = compile_fixture(items)
    assert len(result) == len(before)
    assert result[0]["id"] == before[0]["id"] and result[0]["actor_type"] == before[0]["actor_type"]
    assert result[0]["mentions"][: len(before[0]["mentions"])] == before[0]["mentions"]
    assert result[0]["mentions"][-1]["evidence"]["text"] == "B"
    assert receipt["new_actors"] == 0
    assert not actor_inventory.augmentation_preserves_named(before, result)
    assert actor_inventory.augmentation_preserves_named(before, result, True)


def test_existing_same_anchor_preserves_original_kind_and_id():
    items = fixture(("A🙂",))
    items[-1]["decisions"]["p0"] = {
        "action": "existing",
        "actor_id": items[1][0]["id"],
        "span_id": items[3]["proposal_ledger"][0]["span_ids"][0],
    }
    result, receipt = compile_fixture(items)
    assert result == items[1]
    assert receipt["bindings"][0]["operation"] == "reuse_same_actor_exact_position_preserving_kind"


@pytest.mark.parametrize(
    "change",
    [
        "unknown_actor",
        "wrong_span",
        "missing",
        "unknown_slot",
        "type_conflict",
        "view",
        "aliases",
        "uncertain",
    ],
)
def test_invalid_or_unresolved_referents_cannot_pass(change):
    items = fixture()
    doc, actors, catalog, view, aliases, raw = items
    if change == "unknown_actor":
        raw["decisions"]["p0"] = {
            "action": "existing",
            "actor_id": "outside",
            "span_id": view["proposal_ledger"][0]["span_ids"][0],
        }
    elif change == "wrong_span":
        raw["decisions"]["p0"]["span_id"] = view["proposal_ledger"][1]["span_ids"][0]
    elif change == "missing":
        del raw["decisions"]["p0"]
    elif change == "unknown_slot":
        raw["decisions"]["p0"]["entity_slot"] = "free_model_id"
    elif change == "type_conflict":
        raw["decisions"]["p1"]["actor_type"] = "organization"
    elif change == "view":
        view["proposal_ledger"][0]["span_ids"] = view["proposal_ledger"][1]["span_ids"]
    elif change == "aliases":
        aliases[next(iter(aliases))] = "forged"
    else:
        raw["decisions"]["p0"] = {"action": "uncertain"}
    with pytest.raises(DraftError):
        compile_fixture(items)


@pytest.mark.parametrize("change", ["id", "actor_type", "original_mention", "remove_actor"])
def test_identity_guard_does_not_allow_augmentation_to_rewrite_originals(change):
    original = named()
    altered = copy.deepcopy(original)
    if change == "id":
        altered[0]["id"] = "different"
    elif change == "actor_type":
        altered[0]["actor_type"] = "court"
    elif change == "original_mention":
        altered[0]["mentions"][0]["kind"] = "description"
    else:
        altered.pop()
    assert not actor_inventory.augmentation_preserves_named(original, altered, True)


def test_source_repair_uses_supported_whole_forms_not_chopped_fragments():
    doc = source()
    units = [u["id"] for u in source_units(doc)]
    result = protocol.supported_forms(doc, units, {"forms": ["A🙂 MISSING", "A🙂", "B"]}, ["p0"])
    assert result == {"p0": ["A🙂", "B"]}
    with pytest.raises(DraftError, match="anonymous_whole_form_options_missing"):
        protocol.supported_forms(doc, units, {"forms": ["A🙂 MISSING"]}, ["p0"])


def test_augmentation_requests_closed_referent_decisions_and_keeps_receipts():
    actors = named()
    saved, calls = {}, []

    def infer(messages, fmt, stage):
        calls.append(stage)
        if stage == "anonymous-forms":
            return '{"forms":["B"]}'
        payload = json.loads(messages[-1]["content"])
        view = payload["catalog"]
        return json.dumps(
            {
                "catalog_sha256": view["catalog_sha256"],
                "decisions": {
                    "p0": {
                        "action": "existing",
                        "actor_id": actors[0]["id"],
                        "span_id": view["proposal_ledger"][0]["span_ids"][0],
                    }
                },
            }
        )

    result = protocol.augment(source(), actors, CONTRACT, PROVENANCE, infer, saved.__setitem__)
    assert len(result[0]["mentions"]) == len(actors[0]["mentions"]) + 1
    assert calls == ["anonymous-forms", "anonymous-referents"]
    assert "anonymous-referent-receipt" in saved
