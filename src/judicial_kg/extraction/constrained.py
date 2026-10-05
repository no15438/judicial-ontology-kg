"""V9 server-side schema constraints; source grounding remains mandatory."""

import copy

from judicial_kg.extraction.actor_grounded import (
    CONTRACT_VERSION,
    NORMALIZER_VERSION,
    draft_schema,
    materialize,
    messages,
    unpack_draft,
)
from judicial_kg.extraction.anchored import source_units

PROMPT_FILE = "prompts/qwen-extraction-v9.txt"

__all__ = [
    "CONTRACT_VERSION",
    "NORMALIZER_VERSION",
    "PROMPT_FILE",
    "draft_schema",
    "materialize",
    "messages",
    "unpack_draft",
    "response_format",
]


def response_format(doc, schema):
    """Bind unit IDs to this source; never infer quote positions or identities."""
    bound = copy.deepcopy(schema)
    identifiers = [u["id"] for u in source_units(doc)]
    if not identifiers:
        raise ValueError("Schema-constrained extraction requires source units")

    def visit(node):
        if isinstance(node, dict):
            if node == {"type": "string", "pattern": "^u[0-9]+$"}:
                node.clear()
                node.update(type="string", enum=identifiers.copy())
            else:
                for child in node.values():
                    visit(child)
        elif isinstance(node, list):
            for child in node:
                visit(child)

    visit(bound)
    # llama.cpp's documented schema-constrained JSON format. Failure is explicit;
    # there is no fallback to unconstrained generation or permissive validation.
    return {"type": "json_object", "schema": bound}
