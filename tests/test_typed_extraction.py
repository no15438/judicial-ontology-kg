"""Normalization unit strings and metadata fixtures, not fabricated legal cases."""

import json

import pytest
from test_anchored import CONTRACT, PROVENANCE, draft, source

from judicial_kg.extraction import typed
from judicial_kg.extraction.anchored import GroundingError
from judicial_kg.validation import validate_record


@pytest.mark.parametrize(
    "raw,kind,expected",
    [
        ("2024年2月29日", "date", ("2024-02-29", None)),
        ("2023年2月29日", "date", (None, None)),
        ("2015年12月", "date", (None, None)),
        ("2015年8月和9月", "date", (None, None)),
        ("一年", "date", (None, None)),
        ("2024-01-01至2024-02-01", "date", (None, None)),
        ("2024/1/9", "date", ("2024-01-09", None)),
        ("800,000元", "money", ("800000", "CNY")),
        ("1.25万元", "money", ("12500.00", "CNY")),
        ("1亿元", "money", ("100000000", "CNY")),
        ("人民币42", "money", ("42", "CNY")),
        ("¥42", "money", ("42", None)),
        ("42", "money", ("42", None)),
        ("十万元", "money", (None, None)),
        ("USD42", "money", (None, None)),
        ("1,23元", "money", (None, None)),
        ("1万元和2万元", "money", (None, None)),
        ("年利率6%", "rate", ("6", "percent")),
        ("0.06", "rate", (None, None)),
        ("42", "text", (None, None)),
        (
            "123456789012345678901234567890万元",
            "money",
            ("1234567890123456789012345678900000", "CNY"),
        ),
    ],
)
def test_conservative_normalization_never_invents_precision_or_currency(raw, kind, expected):
    assert typed.normalize(raw, kind) == expected


def typed_draft(predicate):
    value = draft()
    prop = value["contexts"][0]["events"][0]["statements"][0]
    prop["predicate"] = predicate
    prop["value"] = {"source": prop["value"]["source"]}
    return value, prop


def test_ontology_kind_and_raw_preservation_without_model_normalization():
    for predicate, expected in [("event_date", "date"), ("repayment_deadline", "date")]:
        value, _ = typed_draft(predicate)
        record = typed.materialize(json.dumps(value), source(), CONTRACT, PROVENANCE)
        prop = record["statements"][0]
        assert prop["value"] == {"kind": expected, "raw": "42", "normalized": None, "unit": None}
        assert validate_record(record, source(), CONTRACT) == []


def test_model_cannot_supply_inferred_dates_or_override_fixed_type():
    for key, supplied in [("normalized", "2024-01-01"), ("unit", "CNY"), ("kind", "text")]:
        value, prop = typed_draft("event_date")
        prop["value"][key] = supplied
        with pytest.raises(GroundingError):
            typed.materialize(json.dumps(value), source(), CONTRACT, PROVENANCE)


def test_other_retains_explicit_model_kind_and_source_matching_stays_strict():
    value, prop = typed_draft("other")
    with pytest.raises(GroundingError):
        typed.materialize(json.dumps(value), source(), CONTRACT, PROVENANCE)
    prop["value"]["kind"] = "money"
    record = typed.materialize(json.dumps(value), source(), CONTRACT, PROVENANCE)
    assert record["statements"][0]["value"]["normalized"] == "42"
    prop["value"]["source"]["text"] = "2024年1月1日"
    with pytest.raises(GroundingError) as raised:
        typed.materialize(json.dumps(value), source(), CONTRACT, PROVENANCE)
    assert raised.value.issues[0]["code"] == "quote_missing_in_unit"
