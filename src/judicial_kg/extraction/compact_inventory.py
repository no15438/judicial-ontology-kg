"""V16 compact model-facing span aliases, losslessly bound to the full source catalog."""

import copy

from judicial_kg.extraction import actor_inventory, scoped, scoped_incremental, span_first
from judicial_kg.sources.records import canonical_hash

CONTRACT_VERSION = actor_inventory.CONTRACT_VERSION
NORMALIZER_VERSION = actor_inventory.NORMALIZER_VERSION
PROMPT_FILE = "prompts/qwen-extraction-v16.txt"
GENERATION_CONSTRAINT_VERSION = "compact-source-inventory-before-freeze-v1"
GENERATION_CONSTRAINT_SCOPE = actor_inventory.GENERATION_CONSTRAINT_SCOPE
draft_schema = actor_inventory.draft_schema
response_format = actor_inventory.response_format


def compact_view(catalog, schema):
    aliases = {f"s{i}": e["span_id"] for i, e in enumerate(catalog["entries"])}
    reverse = {v: k for k, v in aliases.items()}

    def replace(value):
        if isinstance(value, str):
            return reverse.get(value, value)
        if isinstance(value, dict):
            return {k: replace(v) for k, v in value.items()}
        if isinstance(value, list):
            return [replace(v) for v in value]
        return value

    view = {
        "catalog_sha256": canonical_hash(catalog),
        "proposals": copy.deepcopy(catalog["proposals"]),
        "entries": [
            {"span_id": reverse[e["span_id"]], **e["selector"]} for e in catalog["entries"]
        ],
        "proposal_ledger": replace(catalog["proposal_ledger"]),
        "complete_actor_coverage": False,
    }
    return replace(schema), view, aliases


def generate_header(doc, prompt, contract, provenance, infer, save):
    return actor_inventory.generate_header(
        doc, prompt, contract, provenance, infer, save, request_view=compact_view
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
        generate_body=span_first.generate_body,
        merge=scoped_incremental.merge_body,
        on_header=scoped_incremental.header_diagnostics,
    )
