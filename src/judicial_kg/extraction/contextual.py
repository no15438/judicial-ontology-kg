"""V6: versioned multi-speaker contexts with unchanged strict source grounding."""

from judicial_kg.extraction import typed

PROMPT_FILE = "prompts/qwen-extraction-v6.txt"
CONTRACT_VERSION = "0.2.0"
NORMALIZER_VERSION = typed.NORMALIZER_VERSION
messages = typed.messages


def draft_schema(contract):
    if contract.ontology["version"] != CONTRACT_VERSION:
        raise ValueError("V6 requires the multi-speaker 0.2.0 contract")
    return typed.draft_schema(contract)


def materialize(content, doc, contract, provenance):
    draft_schema(contract)
    return typed.materialize(content, doc, contract, provenance)
