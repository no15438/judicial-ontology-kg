"""Fresh v8 extraction: stricter source-first prompt, unchanged v7 compiler."""

from judicial_kg.extraction.actor_grounded import (
    CONTRACT_VERSION,
    NORMALIZER_VERSION,
    draft_schema,
    materialize,
    messages,
    unpack_draft,
)

PROMPT_FILE = "prompts/qwen-extraction-v8.txt"

__all__ = [
    "CONTRACT_VERSION",
    "NORMALIZER_VERSION",
    "PROMPT_FILE",
    "draft_schema",
    "materialize",
    "messages",
    "unpack_draft",
]
