"""V37: validate program-compiled inventory without the one-anchor generation cap."""

from judicial_kg.extraction import referent_inventory, scoped

CONTRACT_VERSION = referent_inventory.CONTRACT_VERSION
NORMALIZER_VERSION = referent_inventory.NORMALIZER_VERSION
PROMPT_FILE = "prompts/qwen-extraction-v37.txt"
GENERATION_CONSTRAINT_VERSION = "compiled-inventory-context-handoff-v1"
GENERATION_CONSTRAINT_SCOPE = "multiple_validated_mentions_preserved_with_strict_context_contract"
response_format = referent_inventory.response_format


def draft_schema(contract):
    schema = scoped.draft_schema(contract)
    # This cap bounds generated identity anchors in legacy protocols. Here Actor
    # objects are compiled and checked before the model emits a context-only delta.
    # Keep all other shape/source/context constraints and every existing Mention.
    del schema["$defs"]["actor"]["properties"]["mentions"]["maxItems"]
    return schema


def extract(doc, prompt, contract, provenance, infer, save):
    return referent_inventory.extract(
        doc,
        prompt,
        contract,
        provenance,
        infer,
        save,
        allow_collectives=True,
        header_schema_builder=draft_schema,
    )
