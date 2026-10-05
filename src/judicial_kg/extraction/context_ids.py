"""V19: program-owned context IDs with lossless references and immutable actor anchors."""

from judicial_kg.extraction import actor_inventory, compact_inventory, scoped, scoped_incremental
from judicial_kg.extraction import space_inventory as base

CONTRACT_VERSION = base.CONTRACT_VERSION
NORMALIZER_VERSION = base.NORMALIZER_VERSION
PROMPT_FILE = "prompts/qwen-extraction-v19.txt"
GENERATION_CONSTRAINT_VERSION = "program-owned-context-identifiers-v1"
GENERATION_CONSTRAINT_SCOPE = "context_delta_programmatic_ids_and_frozen_actors"
draft_schema = base.draft_schema
response_format = base.response_format


def generate_header(doc, prompt, contract, provenance, infer, save):
    return actor_inventory.generate_header(
        doc,
        prompt,
        contract,
        provenance,
        infer,
        save,
        request_view=compact_inventory.compact_view,
        catalog_builder=base.build_catalog,
        context_only=True,
        context_ids=True,
    )


def extract(doc, prompt, contract, provenance, infer, save):
    return scoped.extract(
        doc,
        prompt,
        contract,
        provenance,
        infer,
        save,
        generate_header=generate_header,
        generate_body=base.generate_body,
        merge=scoped_incremental.merge_body,
        on_header=scoped_incremental.header_diagnostics,
    )
