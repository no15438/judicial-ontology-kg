"""Lexical corrections preserve original supported forms and all unresolved decisions."""

import pytest
from test_anchored import source

from judicial_kg.extraction.checked_forms import apply_replacements, missing_forms
from judicial_kg.extraction.draft import DraftError


def test_noncontiguous_discovery_can_only_be_replaced_by_actual_source_forms():
    doc = source()
    original = {"forms": ["A🙂", "B42"]}
    assert missing_forms(doc, ["u0", "u1"], original["forms"]) == ["p1"]
    result = {"replacements": [{"proposal_id": "p1", "forms": ["B", "42"]}]}
    assert apply_replacements(doc, ["u0", "u1"], original, result) == {"forms": ["A🙂", "B", "42"]}
    assert original == {"forms": ["A🙂", "B42"]}


@pytest.mark.parametrize(
    "rows",
    [
        [],
        [{"proposal_id": "p0", "forms": ["B"]}],
        [{"proposal_id": "p1", "forms": []}],
        [{"proposal_id": "p1", "forms": ["B42"]}],
        [{"proposal_id": "p1", "forms": ["B"]}, {"proposal_id": "p1", "forms": ["42"]}],
    ],
)
def test_missing_forms_cannot_be_dropped_guessed_or_rewrite_other_forms(rows):
    with pytest.raises(DraftError):
        apply_replacements(
            source(), ["u0", "u1"], {"forms": ["A🙂", "B42"]}, {"replacements": rows}
        )


def test_form_corrections_share_document_budget_and_leave_receipts(monkeypatch):
    import json

    from judicial_kg.extraction import actor_inventory, checked_forms

    doc = source()
    schema = actor_inventory.obj({"forms": {"type": "array", "items": {"type": "string"}}})
    fmt = checked_forms.response_format(doc, schema)
    messages = [
        {"role": "system", "content": "source discovery"},
        {"role": "user", "content": '{"source_units":[]}'},
    ]
    calls, saved = [], {}

    def stage(doc, prompt, combined, context, contract, infer, save, index):
        return infer(messages, fmt, f"body-{index:03}-forms")

    monkeypatch.setattr(checked_forms.grouped_mentions, "generate_body", stage)

    def infer(messages, fmt, stage):
        calls.append(stage)
        return json.dumps(
            {"replacements": [{"proposal_id": "p0", "forms": ["B"]}]}
            if stage.endswith("-repair-1")
            else {"forms": ["B42"]}
        )

    def save(key, value):
        saved[key] = value

    budget = {"used": 0}
    for i in range(3):
        result = checked_forms.generate_body(
            doc, "", {}, {"assertion_units": ["u1"]}, None, infer, save, i, budget=budget
        )
        assert json.loads(result) == {"forms": ["B"]}
        receipt = saved[f"body-{i:03}-forms-correction-receipt"]
        assert receipt["original"]["forms"] == ["B42"]
        assert not receipt["counts_as_first_pass"]
    with pytest.raises(DraftError, match="form_correction_budget_exhausted"):
        checked_forms.generate_body(
            doc, "", {}, {"assertion_units": ["u1"]}, None, infer, save, 3, budget=budget
        )
    assert len([c for c in calls if c.endswith("-repair-1")]) == 3
