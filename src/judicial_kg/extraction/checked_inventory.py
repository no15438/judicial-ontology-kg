"""V34 shares lexical checks across discovery stages and owns per-batch property IDs."""

import copy
import json

from judicial_kg.extraction import (
    checked_forms,
    classified_inventory,
    context_first,
    fragment_repairs,
    unique_forms,
)
from judicial_kg.extraction.anchored import source_units
from judicial_kg.extraction.draft import DraftError
from judicial_kg.sources.records import canonical_hash
from judicial_kg.validation_run import strict_json

CONTRACT_VERSION = classified_inventory.CONTRACT_VERSION
NORMALIZER_VERSION = classified_inventory.NORMALIZER_VERSION
PROMPT_FILE = "prompts/qwen-extraction-v34.txt"
GENERATION_CONSTRAINT_VERSION = "all-discovery-source-checks-and-program-owned-property-ids-v1"
GENERATION_CONSTRAINT_SCOPE = "shared_three_corrections_and_lossless_owner_local_id_mapping"
draft_schema = classified_inventory.draft_schema
response_format = classified_inventory.response_format


def namespace_properties(value, stage):
    result = copy.deepcopy(value)
    originals = [("statements", value["statements"]), *value["event_statements"].items()]
    targets = [("statements", result["statements"]), *result["event_statements"].items()]
    ids = [statement["id"] for _, rows in originals for statement in rows]
    if len(ids) != len(set(ids)):
        raise DraftError("duplicate_property_id_within_batch")
    aliases = []
    for (owner, before), (_, after) in zip(originals, targets, strict=True):
        for index, (old, new) in enumerate(zip(before, after, strict=True)):
            new["id"] = (
                "prop_"
                + canonical_hash(
                    {"stage": stage, "owner": owner, "source_index": index, "id": old["id"]}
                )[:48]
            )
            aliases.append(
                {
                    "owner": owner,
                    "source_index": index,
                    "original_id": old["id"],
                    "compiled_id": new["id"],
                    "original_sha256": canonical_hash(old),
                }
            )
    return result, {
        "original_sha256": canonical_hash(value),
        "compiled_sha256": canonical_hash(result),
        "aliases": aliases,
        "only_ids_changed": True,
        "semantic_support_verified": False,
    }


def extract(
    doc,
    prompt,
    contract,
    provenance,
    infer,
    save,
    *,
    header_generator=None,
    anonymous_repair_options=None,
    header_schema_builder=None,
    classifier=None,
    form_router=None,
):
    budget = {"used": 0}

    def infer_canonical(messages, fmt, stage):
        if stage == "anonymous-forms-repair-1":
            messages = copy.deepcopy(messages)
            messages[0]["content"] += (
                "修正的是未具名个体的描述，不能选其已具名亲属的名称来替代该个体。"
                "保留原提议所指对象，只修正其实际连续称谓，不做身份合并。"
            )
        raw = infer(messages, fmt, stage)
        if stage in ("inventory-forms", "anonymous-forms"):
            original = strict_json(raw)
            save(stage + "-initial-discovery-wire", original)
            value, receipt = unique_forms.deduplicate(original, fmt["schema"])
            save(stage + "-initial-query-dedup", receipt)
            return json.dumps(value, ensure_ascii=False)
        if "-properties-owner-" in stage:
            original = strict_json(raw)
            save(stage + "-original-property-ids", original)
            context_first.check_schema(original, fmt["schema"])
            value, receipt = namespace_properties(original, stage)
            context_first.check_schema(value, fmt["schema"])
            save(stage + "-property-id-receipt", receipt)
            return json.dumps(value, ensure_ascii=False)
        return raw

    checked = checked_forms.source_checked_infer(
        doc,
        [u["id"] for u in source_units(doc)],
        infer_canonical,
        save,
        budget=budget,
        repair_options=fragment_repairs.source_fragments,
    )

    anonymous_checked = checked_forms.source_checked_infer(
        doc,
        [u["id"] for u in source_units(doc)],
        infer_canonical,
        save,
        budget=budget,
        repair_options=anonymous_repair_options or fragment_repairs.source_fragments,
    )

    def dispatch(messages, fmt, stage):
        if stage == "anonymous-forms" and anonymous_repair_options is not None:
            value = anonymous_checked(messages, fmt, stage)
            return (
                json.dumps(
                    form_router(strict_json(value), [u["id"] for u in source_units(doc)], stage),
                    ensure_ascii=False,
                )
                if form_router
                else value
            )
        if stage in ("inventory-forms", "anonymous-forms"):
            return checked(messages, fmt, stage)
        return infer_canonical(messages, fmt, stage)

    return classified_inventory.extract(
        doc,
        prompt,
        contract,
        provenance,
        dispatch,
        save,
        budget=budget,
        header_generator=header_generator,
        header_schema_builder=header_schema_builder,
        form_router=form_router,
        classifier=classifier,
    )
