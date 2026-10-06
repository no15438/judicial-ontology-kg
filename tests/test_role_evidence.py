import json

import pytest

from judicial_kg.extraction.actor_inventory import obj
from judicial_kg.extraction.draft import DraftError
from judicial_kg.extraction.role_evidence import RoleEvidenceSelector


def setup(answer):
    role = obj(
        {
            "actor_id": {"enum": ["a"]},
            "evidence_units": {"type": "array", "items": {"enum": ["u0", "u1"]}, "minItems": 1},
        }
    )
    schema = obj({"roles": {"type": "array", "items": {"$ref": "#/$defs/role"}}})
    schema["$defs"] = {"role": role}
    payload = {
        "source_units": [{"id": "u0", "text": "A"}, {"id": "u1", "text": "B"}],
        "actor_catalog": [{"id": "a"}],
        "frozen_context": {"id": "c"},
    }
    messages = [
        {"role": "system", "content": json.dumps(schema, ensure_ascii=False)},
        {"role": "user", "content": json.dumps(payload)},
    ]
    calls = []
    saved = {}

    def infer(m, fmt, stage):
        calls.append((m, fmt, stage))
        return json.dumps({"roles": [{"actor_id": "a"}]} if len(calls) == 1 else {"r0": answer})

    return RoleEvidenceSelector(infer, saved.__setitem__), messages, schema, calls, saved


def test_closed_selection_compiles_only_selected_evidence():
    selector, m, s, calls, saved = setup({"decision": "supported", "units": ["u1"]})
    raw = json.loads(selector.call(m, {"schema": s}, "body-000-events"))
    assert raw["roles"][0]["evidence_units"] == ["u1"]
    assert "evidence_units" not in calls[0][1]["schema"]["$defs"]["role"]["properties"]
    assert "evidence_units" in s["$defs"]["role"]["properties"]
    assert len(calls) == 2
    assert "body-000-events-role-evidence-selection" in saved


@pytest.mark.parametrize(
    "answer",
    [
        {"decision": "supported", "units": [0]},
        {"decision": "supported", "units": ["u9"]},
        {"decision": "uncertain", "units": []},
    ],
)
def test_invalid_or_uncertain_evidence_never_accepted(answer):
    selector, m, s, _, _ = setup(answer)
    with pytest.raises(DraftError):
        selector.call(m, {"schema": s}, "body-000-events")
