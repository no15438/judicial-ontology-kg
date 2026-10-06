"""V26 represents each (source position, actor) binding once by construction."""

import copy
import json

from judicial_kg.extraction import (
    collective_mentions,
    context_first,
    context_ids,
    scoped,
    scoped_incremental,
)
from judicial_kg.sources.records import canonical_hash
from judicial_kg.validation_run import strict_json

CONTRACT_VERSION = context_ids.CONTRACT_VERSION
NORMALIZER_VERSION = context_ids.NORMALIZER_VERSION
PROMPT_FILE = "prompts/qwen-extraction-v26.txt"
GENERATION_CONSTRAINT_VERSION = "unique-position-actor-binding-map-v1"
GENERATION_CONSTRAINT_SCOPE = "collective_bindings_and_frozen_existing_mention_kinds"
draft_schema = context_ids.draft_schema
response_format = context_ids.response_format


def grouped_schema(schema, catalog, actors):
    result = copy.deepcopy(schema)
    item = result["properties"]["mentions"]["items"]["properties"]
    kinds = item["kind"]
    binding = {
        "type": "object",
        "properties": {a: copy.deepcopy(kinds) for a in item["actor_id"]["enum"]},
        "additionalProperties": False,
        "minProperties": 1,
    }
    result.setdefault("$defs", {})["actor_bindings"] = binding
    positions = {}
    for entry in catalog["entries"]:
        selector = {k: entry[k] for k in ("unit_id", "text", "occurrence")}
        fixed = {
            a["id"]: m["kind"] for a in actors for m in a["mentions"] if m["evidence"] == selector
        }
        if fixed:
            scoped_binding = copy.deepcopy(binding)
            for actor, kind in fixed.items():
                scoped_binding["properties"][actor] = {"const": kind}
            positions[entry["span_id"]] = scoped_binding
        else:
            positions[entry["span_id"]] = {"$ref": "#/$defs/actor_bindings"}
    result["properties"]["mentions"] = {
        "type": "object",
        "properties": positions,
        "additionalProperties": False,
    }
    return result


def expand_bindings(result):
    expanded = copy.deepcopy(result)
    expanded["mentions"] = [
        {"span_id": span, "actor_id": actor, "kind": kind}
        for span, bindings in result["mentions"].items()
        for actor, kind in bindings.items()
    ]
    return expanded


def generate_body(
    doc, prompt, combined, context, contract, infer, save, index, *, facts_generator=None
):
    def infer_bindings(messages, fmt, stage):
        if not stage.endswith("-mentions"):
            return infer(messages, fmt, stage)
        original = fmt["schema"]
        payload = strict_json(messages[-1]["content"])
        schema = grouped_schema(original, payload["catalog"], combined["actors"])
        request_messages = copy.deepcopy(messages)
        request_messages[0]["content"] = request_messages[0]["content"].replace(
            json.dumps(original, ensure_ascii=False), json.dumps(schema, ensure_ascii=False)
        ) + (
            "\n本阶段mentions改为对象：{span_id:{actor_id:kind}}。每个位置内同一主体只出现一次，"
            "集合指代允许多个不同主体；已有同主体同位置的kind必须遵守Schema常量。"
            "不要输出旧mentions数组，也不能重复JSON键。proposal_decisions仍逐项覆盖。"
        )
        raw = strict_json(infer(request_messages, {**fmt, "schema": schema}, stage))
        save(stage + "-grouped-wire", raw)
        context_first.check_schema(raw, schema)
        expanded = expand_bindings(raw)
        save(
            stage + "-grouped-receipt",
            {
                "wire_sha256": canonical_hash(raw),
                "expanded_sha256": canonical_hash(expanded),
                "semantic_attribution_verified": False,
            },
        )
        return json.dumps(expanded, ensure_ascii=False)

    return collective_mentions.generate_body(
        doc,
        prompt,
        combined,
        context,
        contract,
        infer_bindings,
        save,
        index,
        **({"facts_generator": facts_generator} if facts_generator else {}),
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
