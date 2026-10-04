"""Source-anchored drafts with deterministic types and conservative normalization."""

import json
import re
from datetime import date
from decimal import Decimal, localcontext

from jsonschema import Draft202012Validator

from judicial_kg.extraction import anchored
from judicial_kg.extraction.draft import DraftError
from judicial_kg.validation_run import strict_json

PROMPT_FILE = "prompts/qwen-extraction-v5.txt"
NORMALIZER_VERSION = "explicit-values-v1"
messages = anchored.messages


def draft_schema(contract):
    schema = anchored.draft_schema(contract)
    for name in ("statement", "event_statement"):
        node = schema["$defs"][name]
        value = node["properties"]["value"]
        for key in ("normalized", "unit"):
            del value["properties"][key]
        value["required"] = ["source"]
        # Fixed predicates already have an ontology type. Asking the model to
        # restate it independently introduced contradictions on partial dates.
        node["allOf"] = [
            {
                "if": {"properties": {"predicate": {"const": "other"}}},
                "then": {"properties": {"value": {"required": ["kind"]}}},
                "else": {"properties": {"value": {"properties": {"kind": False}}}},
            }
        ]
    return schema


def normalize(raw: str, kind: str) -> tuple[str | None, str | None]:
    """Only explicit unambiguous forms; never infer dates, currency or arithmetic."""
    if kind == "date":
        matched = re.fullmatch(
            r"([0-9]{4})(?:年|[-/.])([0-9]{1,2})(?:月|[-/.])([0-9]{1,2})日?", raw
        )
        if matched:
            try:
                return date(*map(int, matched.groups())).isoformat(), None
            except ValueError:
                pass
        return None, None
    number = r"(?:0|[1-9][0-9]*|[1-9][0-9]{0,2}(?:,[0-9]{3})+)(?:\.[0-9]+)?"
    if kind == "money":
        matched = re.fullmatch(
            rf"(?P<prefix>人民币|[￥¥])?(?P<number>{number})(?P<scale>万|亿)?(?P<suffix>元)?",
            raw,
        )
        if matched:
            # A bare scaled number is not enough to infer a currency.
            unit = "CNY" if matched["prefix"] == "人民币" or matched["suffix"] else None
            multiplier = {None: 1, "万": 10000, "亿": 100000000}[matched["scale"]]
            with localcontext() as context:
                context.prec = len(raw) + 10
                amount = Decimal(matched["number"].replace(",", "")) * multiplier
            return format(amount, "f"), unit
    if kind == "rate":
        matched = re.fullmatch(rf"(?:(?:年|月|日)?利率)?({number})[%％]", raw)
        if matched:
            return format(Decimal(matched[1].replace(",", "")), "f"), "percent"
    return None, None


def materialize(content: str, doc: dict, contract, provenance: dict) -> dict:
    try:
        draft = strict_json(content)
    except (ValueError, TypeError):
        raise DraftError("draft_invalid_json") from None
    errors = list(Draft202012Validator(draft_schema(contract)).iter_errors(draft))
    if errors:
        raise anchored.GroundingError(
            [
                {"code": "draft_schema_invalid", "path": "/" + "/".join(map(str, e.path))}
                for e in errors
            ]
        )
    for context in draft["contexts"]:
        statements = list(context["statements"])
        for event in context["events"]:
            statements.extend(event["statements"])
        for statement in statements:
            value = statement["value"]
            kind = contract.ontology["predicate_value_kinds"].get(statement["predicate"])
            value["kind"] = kind if kind is not None else value["kind"]
            value["normalized"], value["unit"] = normalize(value["source"]["text"], value["kind"])
    # Exact source matching still rejects invented raw values. No node is removed,
    # reparented or silently repaired; only omitted mechanical fields are derived.
    return anchored.materialize(json.dumps(draft, ensure_ascii=False), doc, contract, provenance)
