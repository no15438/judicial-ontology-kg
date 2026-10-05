"""V24 binds predicate types to literal-compatible owner-scoped evidence choices."""

import copy
import re
from functools import partial

from judicial_kg.extraction import (
    actor_inventory,
    context_ids,
    mention_stage,
    owner_first,
    scoped,
    scoped_incremental,
    typed,
)
from judicial_kg.extraction.draft import DraftError

CONTRACT_VERSION = context_ids.CONTRACT_VERSION
NORMALIZER_VERSION = context_ids.NORMALIZER_VERSION
PROMPT_FILE = "prompts/qwen-extraction-v24.txt"
GENERATION_CONSTRAINT_VERSION = "owner-and-literal-type-bound-properties-v1"
GENERATION_CONSTRAINT_SCOPE = "predicate_specific_literal_choices_not_semantic_type_proof"
draft_schema = context_ids.draft_schema
response_format = context_ids.response_format


def literal_kinds(text):
    kinds = {"text"}
    if typed.normalize(text, "money")[0] is not None and re.search(r"元|圆|[￥¥]", text):
        kinds.add("money")
    elif re.fullmatch(r"[零〇一二三四五六七八九十百千万亿两]+[元圆]", text):
        kinds.add("money")
    if re.fullmatch(
        r"[0-9０-９零〇一二三四五六七八九十]{2,4}年(?:[0-9０-９一二三四五六七八九十]{1,3}月)?(?:[0-9０-９一二三四五六七八九十]{1,3}日)?",
        text,
    ):
        kinds.add("date")
    if re.search(r"利率|利息|[%％‰]|[0-9一二三四五六七八九十]+(?:倍|分|厘)", text):
        kinds.add("rate")
    return kinds


def property_schema(base, facts, context, catalog):
    identifiers = [e["id"] for e in facts["events"]]
    if len(set(identifiers)) != len(identifiers):
        raise DraftError("duplicate_event_id")

    def statements(definition, units):
        original = base["$defs"][definition]
        predicate_kinds = {}
        for condition in original["allOf"]:
            predicate = condition["if"]["properties"]["predicate"]["const"]
            if predicate != "other":
                predicate_kinds[predicate] = condition["then"]["properties"]["value"]["properties"][
                    "kind"
                ]["const"]
        branches = []
        for predicate, kind in [
            *predicate_kinds.items(),
            *[("other", k) for k in ("text", "money", "date", "rate")],
        ]:
            choices = [
                f"v{i}"
                for i, entry in enumerate(catalog["entries"])
                if entry["selector"]["unit_id"] in units
                and kind in literal_kinds(entry["selector"]["text"])
            ]
            if not choices:
                continue
            node = copy.deepcopy(original)
            node.pop("allOf")
            node["properties"]["predicate"] = {"const": predicate}
            value = {"source": actor_inventory.obj({"span_id": {"enum": choices}})}
            if predicate == "other":
                value["kind"] = {"const": kind}
            node["properties"]["value"] = actor_inventory.obj(value)
            branches.append(node)
        return {"type": "array", "items": {"oneOf": branches} if branches else {"not": {}}}

    return actor_inventory.obj(
        {
            "statements": statements("statement", context["assertion_units"]),
            "event_statements": actor_inventory.obj(
                {
                    e["id"]: statements("event_statement", e["evidence_units"])
                    for e in facts["events"]
                }
            ),
        }
    )


INSTRUCTION = (
    "\n固定predicate的类型由程序推导，不输出kind；仅other必须给kind。"
    "每种predicate只允许Schema中该类型的span_id。普通文字不能当日期，未到庭不代表日期，"
    "非明确金额不能猜金额。词法候选不是语义结论，仍需原文支持；目录不支持的值不编造。"
)


def generate_body(doc, prompt, combined, context, contract, infer, save, index):
    return mention_stage.generate_body(
        doc,
        prompt,
        combined,
        context,
        contract,
        infer,
        save,
        index,
        facts_generator=partial(
            owner_first.generate_facts,
            schema_builder=property_schema,
            property_instruction=INSTRUCTION,
        ),
    )


def extract(doc, prompt, contract, provenance, infer, save):
    return scoped.extract(
        doc,
        prompt,
        contract,
        provenance,
        infer,
        save,
        generate_header=context_ids.generate_header,
        generate_body=generate_body,
        merge=scoped_incremental.merge_body,
        on_header=scoped_incremental.header_diagnostics,
    )
