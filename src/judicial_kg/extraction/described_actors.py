"""V29 permits source-anchored unnamed participants without inventing a name or identity."""

from judicial_kg.extraction import (
    actor_inventory,
    checked_forms,
    compact_inventory,
    context_ids,
    fragment_repairs,
    scoped,
    scoped_incremental,
    space_inventory,
)

CONTRACT_VERSION = context_ids.CONTRACT_VERSION
NORMALIZER_VERSION = context_ids.NORMALIZER_VERSION
PROMPT_FILE = "prompts/qwen-extraction-v29.txt"
GENERATION_CONSTRAINT_VERSION = "named-and-described-actor-inventory-v1"
GENERATION_CONSTRAINT_SCOPE = "literal_description_anchors_not_forced_named_coreference"
draft_schema = context_ids.draft_schema
response_format = context_ids.response_format


def generate_header(doc, prompt, contract, provenance, infer, save):
    return actor_inventory.generate_header(
        doc,
        prompt,
        contract,
        provenance,
        infer,
        save,
        request_view=compact_inventory.compact_view,
        catalog_builder=space_inventory.build_catalog,
        context_only=True,
        context_ids=True,
        descriptive=True,
    )


def extract(doc, prompt, contract, provenance, infer, save):
    budget = {"used": 0}

    def body(*args):
        return checked_forms.generate_body(
            *args, budget=budget, repair_options=fragment_repairs.source_fragments
        )

    return scoped.extract(
        doc,
        prompt,
        contract,
        provenance,
        infer,
        save,
        generate_header=generate_header,
        generate_body=body,
        merge=scoped_incremental.merge_body,
        on_header=scoped_incremental.header_diagnostics,
    )
