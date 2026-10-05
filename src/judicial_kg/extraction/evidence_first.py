"""V22: build literal evidence choices before generating properties, never copy quotes."""

import copy
import json
import re

from judicial_kg.extraction import (
    actor_inventory,
    context_first,
    context_ids,
    mention_stage,
    scoped,
    scoped_incremental,
    span_catalog,
    value_stage,
)
from judicial_kg.extraction.anchored import source_units
from judicial_kg.extraction.draft import DraftError
from judicial_kg.sources.records import canonical_hash
from judicial_kg.validation_run import strict_json

CONTRACT_VERSION = context_ids.CONTRACT_VERSION
NORMALIZER_VERSION = context_ids.NORMALIZER_VERSION
PROMPT_FILE = "prompts/qwen-extraction-v22.txt"
GENERATION_CONSTRAINT_VERSION = "source-evidence-before-facts-v1"
GENERATION_CONSTRAINT_SCOPE = "immutable_source_choices_and_event_owner_checks"
draft_schema = context_ids.draft_schema
response_format = context_ids.response_format

# These are literal candidates, not semantic labels or normalized values. Whole clauses
# remain available so unusual legal expressions are not silently excluded by regexes.
LITERALS = re.compile(
    r"[0-9０-９]{2,4}年[0-9０-９]{1,2}月(?:[0-9０-９]{1,2}日)?"
    r"|[0-9０-９一二三四五六七八九十百千万亿两零〇]+(?:[.,．][0-9０-９]+)*"
    r"(?:万|亿)?(?:元|圆|万元|亿元|%|％|‰|倍|分|厘)"
)


def build_evidence_catalog(doc, context):
    texts = set()
    for unit in source_units(doc):
        if unit["id"] not in context["assertion_units"]:
            continue
        texts.add(unit["text"])
        texts.update(m.group() for m in LITERALS.finditer(unit["text"]))
    return span_catalog.build_catalog(doc, context["assertion_units"], sorted(texts))


def compile_facts(doc, context, catalog, facts):
    if build_evidence_catalog(doc, context) != catalog:
        raise DraftError("fact_evidence_catalog_changed")
    entries = {f"v{i}": entry for i, entry in enumerate(catalog["entries"])}
    result = copy.deepcopy(facts)
    for path, statement, owner_units in value_stage.value_rows(facts, context):
        identifier = statement["value"]["source"]["span_id"]
        if identifier not in entries:
            raise DraftError("fact_evidence_unknown")
        entry = entries[identifier]
        if entry["selector"]["unit_id"] not in owner_units:
            raise DraftError("fact_evidence_outside_owner")
        target = result
        for part in path:
            target = target[part]
        target["value"]["source"] = copy.deepcopy(entry["selector"])
    return result


def generate_facts(doc, prompt, combined, context, contract, compiled_mentions, infer, save, index):
    prefix = f"body-{index:03}"
    catalog = build_evidence_catalog(doc, context)
    save(prefix + "-evidence-catalog", catalog)
    view = [{"span_id": f"v{i}", **entry["selector"]} for i, entry in enumerate(catalog["entries"])]
    schema = scoped.body_schema(combined, context, contract)
    del schema["properties"]["mentions"]
    schema["required"].remove("mentions")
    schema["$defs"]["selector"] = actor_inventory.obj(
        {"span_id": {"type": "string", "enum": [entry["span_id"] for entry in view]}}
    )
    messages = context_first.body_messages(
        doc, prompt, combined, scoped.direct_context(context), schema
    )
    messages[0]["content"] += (
        "\n本阶段只输出roles/events/statements。属性value.source仅选择evidence_catalog的span_id，"
        "禁止抄写或生成text/unit_id/occurrence。每个事件的evidence_units必须包含其全部属性的证据单元，"
        "并确实支持同一事件，不能为了通过校验借用其他事件或其他语境的金额。"
        "选择逐字数字片段用于金额/日期；特殊表达允许选完整原文单元并由程序保守归一化。"
        "other文本属性选择支持它的完整单元，不能把概括冒充原文；无证据不推定。"
        "原告请求、争议、否定、法院认定必须保留当前语境与极性，不等于模型确认成立。"
    )
    payload = strict_json(messages[-1]["content"])
    payload["frozen_context"].pop("evidence_units")
    payload["local_mentions"] = compiled_mentions["mentions"]
    payload["evidence_catalog"] = view
    payload["evidence_catalog_sha256"] = canonical_hash(catalog)
    messages[-1]["content"] = json.dumps(payload, ensure_ascii=False)
    facts = strict_json(infer(messages, response_format(doc, schema), prefix + "-facts"))
    save(prefix + "-facts-wire", facts)
    context_first.check_schema(facts, schema)
    result = compile_facts(doc, context, catalog, facts)
    save(prefix + "-facts-compiled", result)
    save(
        prefix + "-facts-receipt",
        {
            "catalog_sha256": canonical_hash(catalog),
            "wire_sha256": canonical_hash(facts),
            "compiled_sha256": canonical_hash(result),
            "semantic_support_verified": False,
        },
    )
    return result


def generate_body(doc, prompt, combined, context, contract, infer, save, index):
    return mention_stage.generate_body(
        doc, prompt, combined, context, contract, infer, save, index, facts_generator=generate_facts
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
