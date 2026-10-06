"""V36 represents explicit collective references without inventing a group Actor."""

from judicial_kg.extraction import referent_inventory

CONTRACT_VERSION = referent_inventory.CONTRACT_VERSION
NORMALIZER_VERSION = referent_inventory.NORMALIZER_VERSION
PROMPT_FILE = "prompts/qwen-extraction-v36.txt"
GENERATION_CONSTRAINT_VERSION = "closed-collective-existing-referent-bindings-v1"
GENERATION_CONSTRAINT_SCOPE = "one_source_position_multiple_known_actors_not_identity_merge"
draft_schema = referent_inventory.draft_schema
response_format = referent_inventory.response_format


def extract(doc, prompt, contract, provenance, infer, save):
    return referent_inventory.extract(
        doc, prompt, contract, provenance, infer, save, allow_collectives=True
    )
