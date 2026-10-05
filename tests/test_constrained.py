"""Prevent vocabulary/source-ID errors while retaining strict evidence rejection."""

import copy
import json

import pytest
from jsonschema import Draft202012Validator
from test_actor_grounded import CONTRACT, nested
from test_anchored import PROVENANCE, source

from judicial_kg.extraction import constrained
from judicial_kg.extraction.anchored import source_units
from judicial_kg.extraction.draft import DraftError


def test_generation_schema_is_source_bound_without_mutating_static_schema():
    schema = constrained.draft_schema(CONTRACT)
    before = copy.deepcopy(schema)
    response = constrained.response_format(source(), schema)
    assert schema == before
    bound = response["schema"]["$defs"]
    identifiers = [u["id"] for u in source_units(source())]
    assert bound["selector"]["properties"]["unit_id"]["enum"] == identifiers
    for kind in ("context", "role", "event"):
        assert bound[kind]["properties"]["evidence_units"]["items"]["enum"] == identifiers
    assert response["type"] == "json_object"


@pytest.mark.parametrize("role", ["claimant", "respondent", "transferor", "transferee"])
def test_event_participant_roles_cannot_leak_into_context_roles(role):
    _, draft = nested()
    schema = constrained.draft_schema(CONTRACT)
    event = schema["$defs"]["event"]["properties"]["participants"]["items"]
    assert not list(Draft202012Validator(event).iter_errors({"actor_id": "a", "role": role}))
    draft["contexts"][0]["roles"][0]["role"] = role
    with pytest.raises(DraftError):
        constrained.materialize(json.dumps(draft), source(), CONTRACT, PROVENANCE)


def test_backend_ignoring_constraint_cannot_bypass_source_grounding():
    _, draft = nested()
    draft["actors"][0]["mentions"][0]["evidence"]["text"] = "UNSUPPORTED"
    with pytest.raises(DraftError):
        constrained.materialize(json.dumps(draft), source(), CONTRACT, PROVENANCE)
