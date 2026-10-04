"""Source-indexed, context-nested drafts, compiled into the unchanged final contract."""

import copy
import json
import re

from jsonschema import Draft202012Validator

from judicial_kg.contracts import candidate_record
from judicial_kg.extraction.draft import DraftError
from judicial_kg.sources.records import evidence_span
from judicial_kg.validation import validate_source
from judicial_kg.validation_run import strict_json

VERSION = "source-units-v1"
PROMPT_FILE = "prompts/qwen-extraction-v4.txt"


def source_units(doc: dict) -> list[dict]:
    """Lossless clause partition, never a legal-context or sentence classifier."""
    validate_source(doc)
    units = []
    for field in doc["fields"]:
        text, start = field["text"], 0
        for index, char in enumerate(text):
            delimiter = char in "，,。；;！？!?\n"
            # Preserve digit-separated numbers, including comma-grouped amounts.
            if char in "，," and 0 < index < len(text) - 1:
                delimiter = not (text[index - 1].isdigit() and text[index + 1].isdigit())
            if delimiter or index - start + 1 >= 384 or index == len(text) - 1:
                end = index + 1
                units.append(
                    {
                        "id": f"u{len(units)}",
                        "field": field["name"],
                        "start": start,
                        "end": end,
                        "text": text[start:end],
                    }
                )
                start = end
    return units


def draft_schema(contract) -> dict:
    definitions = copy.deepcopy(contract.schema["$defs"])

    def obj(**properties):
        return {
            "type": "object",
            "properties": properties,
            "required": list(properties),
            "additionalProperties": False,
        }

    def array(definition, minimum=0):
        return {
            "type": "array",
            "items": {"$ref": "#/$defs/" + definition},
            "minItems": minimum,
            "uniqueItems": True,
        }

    def remove(node, key):
        del node["properties"][key]
        node["required"].remove(key)

    unit_id = {"type": "string", "pattern": "^u[0-9]+$"}
    ids = {"type": "array", "items": unit_id, "minItems": 1, "uniqueItems": True}
    selector = obj(
        unit_id=unit_id,
        text={"type": "string", "minLength": 1},
        occurrence={"anyOf": [{"type": "integer", "minimum": 0}, {"type": "null"}]},
    )
    del definitions["span"]
    definitions["selector"] = selector
    remove(definitions["actor"], "mention_ids")
    definitions["mention"]["properties"]["evidence"] = {"$ref": "#/$defs/selector"}
    for kind in ("context", "role", "event"):
        remove(definitions[kind], "evidence")
        definitions[kind]["properties"]["evidence_units"] = ids
        definitions[kind]["required"].append("evidence_units")
    for kind in ("role", "event", "statement"):
        remove(definitions[kind], "context_id")
    remove(definitions["statement"], "evidence")
    value = definitions["statement"]["properties"]["value"]
    remove(value, "raw")
    value["properties"]["source"] = {"$ref": "#/$defs/selector"}
    value["required"].append("source")
    definitions["event_statement"] = copy.deepcopy(definitions["statement"])
    remove(definitions["event_statement"], "subject_id")
    definitions["event"]["properties"]["statements"] = array("event_statement")
    definitions["event"]["required"].append("statements")
    for collection, definition in (
        ("roles", "role"),
        ("events", "event"),
        ("statements", "statement"),
    ):
        definitions["context"]["properties"][collection] = array(definition)
        definitions["context"]["required"].append(collection)
    return {
        "$schema": "https://json-schema.org/draft/2020-12/schema",
        **obj(actors=array("actor"), mentions=array("mention"), contexts=array("context")),
        "$defs": definitions,
    }


def messages(doc: dict, prompt: str, schema: dict) -> list[dict]:
    fields = [{"id": u["id"], "field": u["field"], "text": u["text"]} for u in source_units(doc)]
    return [
        {
            "role": "system",
            "content": prompt
            + "\nJSON Schema:\n"
            + json.dumps(schema, ensure_ascii=False, separators=(",", ":")),
        },
        {"role": "user", "content": json.dumps({"source_units": fields}, ensure_ascii=False)},
    ]


class GroundingError(DraftError):
    def __init__(self, issues):
        super().__init__("draft_grounding_failed")
        self.issues = issues


def materialize(content: str, doc: dict, contract, provenance: dict) -> dict:
    try:
        draft = strict_json(content)
    except (ValueError, TypeError):
        raise DraftError("draft_invalid_json") from None
    schema_errors = list(Draft202012Validator(draft_schema(contract)).iter_errors(draft))
    if schema_errors:
        raise GroundingError(
            [
                {"code": "draft_schema_invalid", "path": "/" + "/".join(map(str, e.path))}
                for e in schema_errors
            ]
        )
    units = {u["id"]: u for u in source_units(doc)}
    issues = []

    def error(code, path):
        issues.append({"code": code, "path": path})

    def spans(ids, path, scope=None):
        result = []
        for i, identifier in enumerate(ids):
            unit = units.get(identifier)
            if unit is None:
                error("unknown_evidence_unit", f"{path}/{i}")
                continue
            if scope is not None and identifier not in scope:
                error("evidence_outside_context", f"{path}/{i}")
            result.append(evidence_span(doc, unit["field"], unit["start"], unit["end"]))
        return result

    def quote(selector, path, scope=None):
        unit = units.get(selector["unit_id"])
        if unit is None:
            error("unknown_evidence_unit", path)
            return None
        if scope is not None and unit["id"] not in scope:
            error("evidence_outside_context", path)
        needle, occurrence = selector["text"], selector["occurrence"]
        starts = [m.start() for m in re.finditer("(?=" + re.escape(needle) + ")", unit["text"])]
        if not starts:
            error("quote_missing_in_unit", path)
            return None
        if occurrence is None:
            if len(starts) != 1:
                error("quote_ambiguous_in_unit", path)
                return None
            occurrence = 0
        if not 0 <= occurrence < len(starts):
            error("quote_occurrence_invalid", path)
            return None
        start = unit["start"] + starts[occurrence]
        return evidence_span(doc, unit["field"], start, start + len(needle))

    record = candidate_record(doc, provenance, [f["name"] for f in doc["fields"]])
    record["actors"] = copy.deepcopy(draft["actors"])
    for index, mention in enumerate(draft["mentions"]):
        span = quote(mention["evidence"], f"/mentions/{index}/evidence")
        record["mentions"].append({**mention, "evidence": span})

    def statement(node, context, scope, path, subject=None):
        selector = node["value"]["source"]
        span = quote(selector, path + "/value/source", scope)
        # The model chooses the raw substring and its unit. It cannot separately
        # copy an unsupported raw value and attach evidence that omits that value.
        evidence = spans([selector["unit_id"]], path + "/value/source", scope)
        value = {k: v for k, v in node["value"].items() if k != "source"}
        value["raw"] = span["quote"] if span else selector["text"]
        output = {**node, "value": value, "context_id": context, "evidence": evidence}
        output["subject_id"] = subject if subject is not None else node["subject_id"]
        if subject is None and output["subject_id"] not in actor_ids:
            error("context_statement_subject_must_be_actor", path + "/subject_id")
        record["statements"].append(output)

    actor_ids = {a["id"] for a in record["actors"]}
    for index, context in enumerate(draft["contexts"]):
        path, scope = f"/contexts/{index}", context["evidence_units"]
        node = {
            k: v
            for k, v in context.items()
            if k not in {"roles", "events", "statements", "evidence_units"}
        }
        node["evidence"] = spans(scope, path + "/evidence_units")
        record["contexts"].append(node)
        for kind in ("roles", "events"):
            for item_index, item in enumerate(context[kind]):
                child_path = f"{path}/{kind}/{item_index}"
                child = {k: v for k, v in item.items() if k not in {"evidence_units", "statements"}}
                child.update(
                    context_id=context["id"],
                    evidence=spans(item["evidence_units"], child_path + "/evidence_units", scope),
                )
                record[kind].append(child)
                if kind == "events":
                    for j, prop in enumerate(item["statements"]):
                        statement(
                            prop,
                            context["id"],
                            scope,
                            child_path + f"/statements/{j}",
                            subject=item["id"],
                        )
        for j, prop in enumerate(context["statements"]):
            statement(prop, context["id"], scope, path + f"/statements/{j}")
    if issues:
        raise GroundingError(issues)
    seen = set()
    for i, mention in enumerate(record["mentions"]):
        span = mention["evidence"]
        key = (mention["actor_id"], span["field"], span["start"], span["end"])
        if key in seen:
            error("duplicate_mention_evidence", f"/mentions/{i}")
        seen.add(key)
    if issues:
        raise GroundingError(issues)
    for actor in record["actors"]:
        actor["mention_ids"] = [m["id"] for m in record["mentions"] if m["actor_id"] == actor["id"]]
    return record
