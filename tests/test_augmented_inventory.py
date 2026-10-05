"""Anonymous augmentation cannot rename or overwrite the accepted named inventory."""

import copy
import json

from test_actor_inventory import catalog, selection
from test_anchored import PROVENANCE, source
from test_scoped import CONTRACT

from judicial_kg.extraction import actor_inventory, augmented_inventory


def named():
    c = catalog()
    return actor_inventory.compile_inventory(source(), c, selection(c), CONTRACT, PROVENANCE)[
        "actors"
    ]


def test_anonymous_local_id_collision_is_namespaced_and_original_names_unchanged():
    actors = named()
    before = copy.deepcopy(actors)
    saved, calls = {}, []

    def infer(messages, fmt, stage):
        calls.append(stage)
        if stage == "anonymous-forms":
            return '{"forms":["B"]}'
        payload = json.loads(messages[-1]["content"])
        value = selection(payload["catalog"])
        value["actors"][0]["anchor_kind"] = "description"
        return json.dumps(value)

    def save(k, v):
        saved[k] = v

    result = augmented_inventory.augment(source(), actors, CONTRACT, PROVENANCE, infer, save)
    assert result[: len(actors)] == before == actors
    assert result[-1]["id"].startswith("anon_")
    assert result[-1]["id"] != actors[0]["id"]
    assert result[-1]["mentions"][0]["kind"] == "description"
    assert result[-1]["mentions"][0]["evidence"]["text"] == "B"
    assert not saved["anonymous-identity-review"]["identity_equivalence_verified"]
    assert not saved["anonymous-identity-review"]["separate_ids_prove_distinct_people"]
    assert calls == ["anonymous-forms", "anonymous-select"]


def test_empty_anonymous_discovery_does_not_regenerate_named_actors():
    actors = named()
    calls = []

    def infer(messages, fmt, stage):
        calls.append(stage)
        return '{"forms":[]}'

    result = augmented_inventory.augment(
        source(), actors, CONTRACT, PROVENANCE, infer, lambda k, v: None
    )
    assert result == actors
    assert result is not actors
    assert calls == ["anonymous-forms"]
