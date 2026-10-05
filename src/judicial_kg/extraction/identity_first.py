"""V11: one grounded identity anchor per Actor; local mentions generated with each body."""

import copy
import json

from judicial_kg.extraction import context_first as base
from judicial_kg.extraction.anchored import GroundingError, source_units
from judicial_kg.sources.records import canonical_hash
from judicial_kg.validation_run import strict_json

CONTRACT_VERSION = base.CONTRACT_VERSION
NORMALIZER_VERSION = base.NORMALIZER_VERSION
PROMPT_FILE = "prompts/qwen-extraction-v11.txt"
GENERATION_CONSTRAINT_VERSION = "identity-anchor-then-local-mentions-v1"
GENERATION_CONSTRAINT_SCOPE = "single_identity_anchor_then_frozen_context_local_mentions_and_bodies"
messages = base.messages
response_format = base.response_format


def draft_schema(contract):
    schema = base.draft_schema(contract)
    schema["$defs"]["actor"]["properties"]["mentions"]["maxItems"] = 1
    return schema


def body_schema(header, context, contract):
    schema = base.body_schema(header, context, contract)
    mention = schema["$defs"]["mention"]
    mention["properties"]["actor_id"] = {
        "type": "string",
        "enum": [a["id"] for a in header["actors"]],
    }
    mention["required"].append("actor_id")
    schema["properties"]["mentions"] = {
        "type": "array",
        "items": {"$ref": "#/$defs/mention"},
        "uniqueItems": True,
    }
    schema["required"].append("mentions")
    return schema


def body_messages(doc, prompt, catalog, context, schema):
    result = base.body_messages(doc, prompt, catalog, context, schema)
    result[0]["content"] += (
        "\n本版本子阶段还必须输出mentions数组，条目含actor_id/id/kind/evidence。"
        "提及必须逐字在本语境source_units里出现；省略主语不能生成姓名。"
        "原文是‘原告’就只能复制‘原告’，不能替换成主体姓名。"
        "已知主体身份不证明其姓名在本片段出现；其他人名也不能改为该主体。"
        "补充本语境所有可确定的真实提及，不重复catalog中的任何已校验坐标。"
        "不能创建新Actor或删改已有主体/提及。确实没有新增文字提及时填空数组。"
    )
    payload = strict_json(result[-1]["content"])
    for entry, actor in zip(payload["actor_catalog"], catalog["actors"], strict=True):
        entry["existing_mention_selectors"] = [m["evidence"] for m in actor["mentions"]]
    result[-1]["content"] = json.dumps(payload, ensure_ascii=False)
    return result


def merge_body(combined, index, body, namespace="v11"):
    body = copy.deepcopy(body)
    mentions = body.pop("mentions")
    # One local ID namespace includes mentions and assertions; reject collisions.
    nodes = mentions + [n for k in base.CHILDREN for n in body[k]]
    nodes.extend(s for e in body["events"] for s in e["statements"])
    if len({n["id"] for n in nodes}) != len(nodes):
        raise GroundingError([{"code": "duplicate_child_id", "path": "/"}])
    result = base.merge_body(combined, index, body, namespace=namespace)
    actors = {a["id"]: a for a in result["actors"]}
    for mention in mentions:
        actor_id = mention.pop("actor_id")
        mention["id"] = f"{namespace}_b{index}_{mention['id']}"
        actors[actor_id]["mentions"].append(mention)
    return result


def extract(doc, prompt, contract, provenance, infer, save):
    schema = draft_schema(contract)
    header = strict_json(
        infer(messages(doc, prompt, schema), response_format(doc, schema), "header")
    )
    save("header-draft", header)
    base.check_schema(header, schema)
    base.compile_checked(header, doc, contract, provenance)
    plan = {
        "header_sha256": canonical_hash(header),
        "source_units_total": len(source_units(doc)),
        "unassigned_source_unit_ids": [
            u["id"]
            for u in source_units(doc)
            if u["id"] not in {i for c in header["contexts"] for i in c["evidence_units"]}
        ],
        "coverage_is_semantic_recall": False,
        "identity_mentions_are_representative_anchors": True,
        "contexts": [
            {"context_id": c["id"], "status": "not_attempted"} for c in header["contexts"]
        ],
        "complete": False,
    }
    save("stage-plan", plan)
    combined = copy.deepcopy(header)
    for index, context in enumerate(header["contexts"]):
        row = plan["contexts"][index]
        try:
            schema = body_schema(header, context, contract)
            body = strict_json(
                infer(
                    body_messages(doc, prompt, combined, context, schema),
                    response_format(doc, schema),
                    f"body-{index:03}",
                )
            )
            save(f"body-{index:03}-draft", body)
            base.check_schema(body, schema)
            proposed = merge_body(combined, index, body)
            base.compile_checked(proposed, doc, contract, provenance)
            combined = proposed
            row["status"] = "validated"
        except Exception:
            row["status"] = "failed"
            raise
        finally:
            save("stage-plan", plan)
    record = base.compile_checked(combined, doc, contract, provenance)
    plan["complete"] = True
    save("stage-plan", plan)
    save("combined-draft", combined)
    return record
