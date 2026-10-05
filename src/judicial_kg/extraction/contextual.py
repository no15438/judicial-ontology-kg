"""V6: versioned multi-speaker contexts with unchanged strict source grounding."""

import json

from judicial_kg.extraction import typed
from judicial_kg.extraction.anchored import GroundingError
from judicial_kg.extraction.draft import DraftError
from judicial_kg.validation_run import strict_json

PROMPT_FILE = "prompts/qwen-extraction-v6.txt"
CONTRACT_VERSION = "0.2.0"
NORMALIZER_VERSION = typed.NORMALIZER_VERSION
messages = typed.messages


def draft_schema(contract):
    if contract.ontology["version"] != CONTRACT_VERSION:
        raise ValueError("V6 requires the multi-speaker 0.2.0 contract")
    return typed.draft_schema(contract)


def unpack_draft(content):
    try:
        draft = strict_json(content)
    except (ValueError, TypeError):
        raise DraftError("draft_invalid_json") from None
    # A redundant type is harmless only when exactly one referenced Actor has
    # that same type. Never infer an Actor or resolve contradictory declarations.
    if isinstance(draft, dict) and isinstance(draft.get("mentions"), list):
        actors = draft.get("actors", [])
        for index, mention in enumerate(draft["mentions"]):
            if not isinstance(mention, dict) or "actor_type" not in mention:
                continue
            matches = (
                [
                    a
                    for a in actors
                    if isinstance(a, dict) and "id" in a and a["id"] == mention.get("actor_id")
                ]
                if isinstance(actors, list)
                else []
            )
            if len(matches) != 1 or mention["actor_type"] != matches[0].get("actor_type"):
                raise GroundingError(
                    [
                        {
                            "code": "redundant_actor_type_conflict",
                            "path": f"/mentions/{index}/actor_type",
                        }
                    ]
                )
            del mention["actor_type"]
    return draft


def materialize(content, doc, contract, provenance):
    draft_schema(contract)
    return typed.materialize(
        json.dumps(unpack_draft(content), ensure_ascii=False), doc, contract, provenance
    )
