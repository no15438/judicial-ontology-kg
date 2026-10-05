"""V17 source-preserving space variants remain explicit model selection options."""

from functools import partial

from judicial_kg.extraction import (
    actor_inventory,
    compact_inventory,
    scoped,
    scoped_incremental,
    span_catalog,
    span_first,
)

CONTRACT_VERSION = actor_inventory.CONTRACT_VERSION
NORMALIZER_VERSION = actor_inventory.NORMALIZER_VERSION
PROMPT_FILE = "prompts/qwen-extraction-v17.txt"
GENERATION_CONSTRAINT_VERSION = "source-space-variants-explicit-choice-v1"
GENERATION_CONSTRAINT_SCOPE = actor_inventory.GENERATION_CONSTRAINT_SCOPE
draft_schema = actor_inventory.draft_schema
response_format = actor_inventory.response_format
build_catalog = partial(span_catalog.build_catalog, space_variants=True)


def generate_header(doc, prompt, contract, provenance, infer, save):
    return actor_inventory.generate_header(
        doc,
        prompt,
        contract,
        provenance,
        infer,
        save,
        request_view=compact_inventory.compact_view,
        catalog_builder=build_catalog,
    )


def generate_body(doc, prompt, combined, context, contract, infer, save, index):
    return span_first.generate_body(
        doc, prompt, combined, context, contract, infer, save, index, catalog_builder=build_catalog
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
        generate_body=generate_body,
        merge=scoped_incremental.merge_body,
        on_header=scoped_incremental.header_diagnostics,
    )
