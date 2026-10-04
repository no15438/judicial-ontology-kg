"""Model quotes become provenance spans only after exact source lookup."""

import copy
import json

from jsonschema import Draft202012Validator

from judicial_kg.contracts import COLLECTIONS, Contract, candidate_record
from judicial_kg.sources.records import evidence_span
from judicial_kg.validation_run import strict_json


class DraftError(ValueError):
    def __init__(self, code: str, path: str = "/", issues=None):
        self.code, self.path = code, path
        self.issues = issues if issues is not None else [{"code": code, "path": path}]
        super().__init__(code)


def draft_schema(contract: Contract) -> dict:
    schema = copy.deepcopy(contract.schema)
    schema["properties"] = {key: schema["properties"][key] for key in COLLECTIONS}
    schema["required"] = list(COLLECTIONS)
    # The model supplies the forward mention->actor link only. Inverse IDs are
    # deterministic bookkeeping and caused unbounded placeholder-ID generation.
    del schema["$defs"]["actor"]["properties"]["mention_ids"]
    schema["$defs"]["actor"]["required"].remove("mention_ids")
    schema["$defs"]["span"] = {
        "type": "object",
        "properties": {
            "field": {"type": "string", "minLength": 1},
            "quote": {"type": "string", "minLength": 1},
            "occurrence": {"anyOf": [{"type": "integer", "minimum": 0}, {"type": "null"}]},
        },
        "required": ["field", "quote", "occurrence"],
        "additionalProperties": False,
    }
    return schema


def messages(doc: dict, prompt: str, schema: dict) -> list[dict]:
    # Do not send upstream QA answers, source labels, or reference annotations to Qwen.
    source = {"fields": [{"name": f["name"], "text": f["text"]} for f in doc["fields"]]}
    return [
        {"role": "system", "content": prompt + "\nJSON Schema:\n" + json.dumps(schema)},
        {"role": "user", "content": json.dumps(source, ensure_ascii=False)},
    ]


def resolve_quote(doc: dict, quote: dict) -> dict:
    fields = [f for f in doc["fields"] if f["name"] == quote["field"]]
    if len(fields) != 1:
        raise DraftError("quote_field_not_found")
    text, needle = fields[0]["text"], quote["quote"]
    if not needle:
        raise DraftError("quote_empty")
    starts, cursor = [], 0
    while (cursor := text.find(needle, cursor)) != -1:
        starts.append(cursor)
        cursor += 1  # Include overlapping occurrences; Unicode code points, not bytes.
    if not starts:
        raise DraftError("quote_missing")
    occurrence = quote["occurrence"]
    if occurrence is None:
        if len(starts) != 1:
            raise DraftError("quote_ambiguous")
        occurrence = 0
    if type(occurrence) is not int or not 0 <= occurrence < len(starts):
        raise DraftError("quote_occurrence_invalid")
    start = starts[occurrence]
    return evidence_span(doc, quote["field"], start, start + len(needle))


def materialize(content: str, doc: dict, contract: Contract, provenance: dict) -> dict:
    try:
        draft = strict_json(content)
    except (ValueError, TypeError):
        raise DraftError("draft_invalid_json") from None
    errors = list(Draft202012Validator(draft_schema(contract)).iter_errors(draft))
    if errors:
        issues = [
            {"code": "draft_schema_invalid", "path": "/" + "/".join(map(str, e.path))}
            for e in errors
        ]
        raise DraftError("draft_schema_invalid", issues[0]["path"], issues)
    record = candidate_record(doc, provenance, [f["name"] for f in doc["fields"]])
    grounding_issues = []

    def resolve(value, path):
        try:
            return resolve_quote(doc, value)
        except DraftError as error:
            grounding_issues.append({"code": error.code, "path": path})
            return None

    for key in COLLECTIONS:
        record[key] = copy.deepcopy(draft[key])
        if key == "actors":
            continue
        for index, node in enumerate(record[key]):
            path = f"/{key}/{index}/evidence"
            if key == "mentions":
                node["evidence"] = resolve(node["evidence"], path)
            else:
                node["evidence"] = [
                    resolve(value, f"{path}/{j}") for j, value in enumerate(node["evidence"])
                ]
    if grounding_issues:
        first = grounding_issues[0]
        raise DraftError(first["code"], first["path"], grounding_issues)
    seen = set()
    for mention in record["mentions"]:
        span = mention["evidence"]
        key = (mention["actor_id"], span["field"], span["start"], span["end"])
        if key in seen:
            raise DraftError("duplicate_mention_evidence", "/mentions")
        seen.add(key)
    for actor in record["actors"]:
        actor["mention_ids"] = [m["id"] for m in record["mentions"] if m["actor_id"] == actor["id"]]
    return record
