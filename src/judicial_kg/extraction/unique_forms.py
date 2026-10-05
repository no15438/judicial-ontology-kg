"""V31 canonicalizes repeated discovery queries while retaining every source occurrence."""

import copy
import json

from judicial_kg.extraction import augmented_inventory, context_first
from judicial_kg.sources.records import canonical_hash
from judicial_kg.validation_run import strict_json

CONTRACT_VERSION = augmented_inventory.CONTRACT_VERSION
NORMALIZER_VERSION = augmented_inventory.NORMALIZER_VERSION
# Prompt intentionally unchanged: this is a deterministic compiler change, not new instructions.
PROMPT_FILE = "prompts/qwen-extraction-v31.txt"
GENERATION_CONSTRAINT_VERSION = "exact_discovery_query_dedup_with_occurrence_ledger-v1"
GENERATION_CONSTRAINT_SCOPE = "deduplicate_identical_queries_not_mentions_or_actors"
draft_schema = augmented_inventory.draft_schema
response_format = augmented_inventory.response_format


def deduplicate(value, schema):
    permissive = copy.deepcopy(schema)
    permissive["properties"]["forms"]["uniqueItems"] = False
    context_first.check_schema(value, permissive)
    forms, indices = [], {}
    for index, text in enumerate(value["forms"]):
        if text not in indices:
            forms.append(text)
            indices[text] = []
        indices[text].append(index)
    result = {"forms": forms}
    context_first.check_schema(result, schema)
    return result, {
        "original_sha256": canonical_hash(value),
        "result_sha256": canonical_hash(result),
        "input_count": len(value["forms"]),
        "output_count": len(forms),
        "source_indices": [indices[text] for text in forms],
        "matching_rule": "exact_unicode_equality_no_whitespace_or_identity_normalization",
        "all_source_occurrences_still_enumerated": True,
    }


def extract(doc, prompt, contract, provenance, infer, save):
    def checked(messages, fmt, stage):
        raw = infer(messages, fmt, stage)
        if not stage.endswith("-forms"):
            return raw
        value = strict_json(raw)
        save(stage + "-original-queries", value)
        result, receipt = deduplicate(value, fmt["schema"])
        save(stage + "-query-dedup", receipt)
        return json.dumps(result, ensure_ascii=False)

    return augmented_inventory.extract(doc, prompt, contract, provenance, checked, save)
