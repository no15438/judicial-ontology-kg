"""V7 actor-local mentions prevent a separate ungrounded actor-list phase."""

import copy
import json

from jsonschema import Draft202012Validator

from judicial_kg.extraction import contextual
from judicial_kg.extraction.anchored import GroundingError
from judicial_kg.extraction.draft import DraftError
from judicial_kg.validation_run import strict_json

CONTRACT_VERSION = contextual.CONTRACT_VERSION
NORMALIZER_VERSION = contextual.NORMALIZER_VERSION
PROMPT_FILE = "prompts/qwen-extraction-v7.txt"
messages = contextual.messages


def draft_schema(contract):
    schema = contextual.draft_schema(contract)
    mention = schema["$defs"]["mention"]
    del mention["properties"]["actor_id"]
    mention["required"].remove("actor_id")
    actor = schema["$defs"]["actor"]
    actor["properties"]["mentions"] = {
        "type": "array",
        "items": {"$ref": "#/$defs/mention"},
        "minItems": 1,
        "uniqueItems": True,
    }
    actor["required"].append("mentions")
    del schema["properties"]["mentions"]
    schema["required"].remove("mentions")
    return schema


def unpack_draft(content, contract):
    try:
        draft = strict_json(content)
    except (ValueError, TypeError):
        raise DraftError("draft_invalid_json") from None
    errors = list(Draft202012Validator(draft_schema(contract)).iter_errors(draft))
    if errors:
        raise GroundingError(
            [
                {"code": "draft_schema_invalid", "path": "/" + "/".join(map(str, e.path))}
                for e in errors
            ]
        )
    normalized = {"actors": [], "mentions": [], "contexts": copy.deepcopy(draft["contexts"])}
    for actor in draft["actors"]:
        normalized["actors"].append({k: v for k, v in actor.items() if k != "mentions"})
        normalized["mentions"].extend(
            {**copy.deepcopy(m), "actor_id": actor["id"]} for m in actor["mentions"]
        )
    return normalized


def materialize(content, doc, contract, provenance):
    return contextual.materialize(
        json.dumps(unpack_draft(content, contract), ensure_ascii=False), doc, contract, provenance
    )
