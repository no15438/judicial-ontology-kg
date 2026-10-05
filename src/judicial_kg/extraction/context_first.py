"""V10 source-only extraction: validated identities/contexts, then frozen-context bodies."""

import copy
import json

from jsonschema import Draft202012Validator

from judicial_kg.extraction import actor_grounded, constrained
from judicial_kg.extraction.anchored import GroundingError, source_units
from judicial_kg.sources.records import canonical_hash
from judicial_kg.validation import validate_record
from judicial_kg.validation_run import strict_json

CONTRACT_VERSION = actor_grounded.CONTRACT_VERSION
NORMALIZER_VERSION = actor_grounded.NORMALIZER_VERSION
PROMPT_FILE = "prompts/qwen-extraction-v10.txt"
GENERATION_CONSTRAINT_VERSION = "validated-header-frozen-context-bodies-v1"
GENERATION_CONSTRAINT_SCOPE = "explicit_occurrence_header_then_per_context_units_and_actor_ids"
CHILDREN = ("roles", "events", "statements")
messages = actor_grounded.messages
response_format = constrained.response_format
materialize = actor_grounded.materialize


def explicit_schema(contract):
    schema = actor_grounded.draft_schema(contract)
    schema["$defs"]["selector"]["properties"]["occurrence"] = {"type": "integer", "minimum": 0}
    return schema


def draft_schema(contract):
    schema = explicit_schema(contract)
    schema["properties"]["actors"]["minItems"] = 1
    schema["properties"]["contexts"]["minItems"] = 1
    # Bound generated contexts and therefore request count, not semantic coverage.
    schema["properties"]["contexts"]["maxItems"] = 32
    for kind in CHILDREN:
        schema["$defs"]["context"]["properties"][kind]["maxItems"] = 0
    return schema


def check_schema(value, schema):
    errors = list(Draft202012Validator(schema).iter_errors(value))
    if errors:
        raise GroundingError(
            [
                {"code": "draft_schema_invalid", "path": "/" + "/".join(map(str, e.path))}
                for e in errors
            ]
        )


def compile_checked(value, doc, contract, provenance):
    record = materialize(json.dumps(value, ensure_ascii=False), doc, contract, provenance)
    issues = validate_record(record, doc, contract)
    if issues:
        error = GroundingError(issues)
        error.code = "contract_rejected"
        raise error
    return record


def body_schema(header, context, contract):
    schema = explicit_schema(contract)
    context_props = schema["$defs"]["context"]["properties"]
    schema["properties"] = {k: copy.deepcopy(context_props[k]) for k in CHILDREN}
    schema["required"] = list(CHILDREN)
    actors = [a["id"] for a in header["actors"]]
    schema["$defs"]["role"]["properties"]["actor_id"] = {"type": "string", "enum": actors}
    schema["$defs"]["statement"]["properties"]["subject_id"] = {"type": "string", "enum": actors}
    participants = schema["$defs"]["event"]["properties"]["participants"]["items"]
    participants["properties"]["actor_id"] = {"type": "string", "enum": actors}
    identifiers = context["evidence_units"]

    def bind(node):
        if isinstance(node, dict):
            if node == {"type": "string", "pattern": "^u[0-9]+$"}:
                node.clear()
                node.update(type="string", enum=identifiers.copy())
            else:
                for child in node.values():
                    bind(child)
        elif isinstance(node, list):
            for child in node:
                bind(child)

    bind(schema)
    return schema


def body_messages(doc, prompt, header, context, schema):
    units = [
        {k: u[k] for k in ("id", "field", "text")}
        for u in source_units(doc)
        if u["id"] in context["evidence_units"]
    ]
    # Names are catalogued identity hints, not child-evidence permissions.
    actors = [
        {
            "id": a["id"],
            "actor_type": a["actor_type"],
            "grounded_mentions": [
                {"kind": m["kind"], "text": m["evidence"]["text"]} for m in a["mentions"]
            ],
        }
        for a in header["actors"]
    ]
    frozen = {k: v for k, v in context.items() if k not in CHILDREN}
    return [
        {
            "role": "system",
            "content": prompt + "\n当前是子阶段：只输出roles/events/statements。"
            "语境和主体目录不可修改。目录名称不是本语境文字证据。不得引用别的语境；"
            "说理与裁判主文须分别记录，不合并为跨语境事件。\nJSON Schema:\n"
            + json.dumps(schema, ensure_ascii=False, separators=(",", ":")),
        },
        {
            "role": "user",
            "content": json.dumps(
                {"frozen_context": frozen, "actor_catalog": actors, "source_units": units},
                ensure_ascii=False,
            ),
        },
    ]


def merge_body(header, context_index, body, namespace="v10"):
    """Namespace local child IDs; never alter actors, context ownership or evidence."""
    result, body = copy.deepcopy(header), copy.deepcopy(body)
    nodes = [n for k in CHILDREN for n in body[k]]
    nodes.extend(s for e in body["events"] for s in e["statements"])
    ids = [n["id"] for n in nodes]
    if len(set(ids)) != len(ids):
        raise GroundingError([{"code": "duplicate_child_id", "path": "/"}])
    for node in nodes:
        node["id"] = f"{namespace}_b{context_index}_{node['id']}"
    result["contexts"][context_index].update(body)
    return result


def extract(doc, prompt, contract, provenance, infer, save):
    schema = draft_schema(contract)
    content = infer(messages(doc, prompt, schema), response_format(doc, schema), "header")
    header = strict_json(content)
    save("header-draft", header)
    check_schema(header, schema)
    compile_checked(header, doc, contract, provenance)
    plan = {
        "header_sha256": canonical_hash(header),
        "source_units_total": len(source_units(doc)),
        "unassigned_source_unit_ids": [
            u["id"]
            for u in source_units(doc)
            if u["id"] not in {i for c in header["contexts"] for i in c["evidence_units"]}
        ],
        "coverage_is_semantic_recall": False,
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
            content = infer(
                body_messages(doc, prompt, header, context, schema),
                response_format(doc, schema),
                f"body-{index:03}",
            )
            body = strict_json(content)
            save(f"body-{index:03}-draft", body)
            check_schema(body, schema)
            proposed = merge_body(combined, index, body)
            compile_checked(proposed, doc, contract, provenance)
            combined = proposed
            row["status"] = "validated"
        except Exception:
            row["status"] = "failed"
            raise
        finally:
            save("stage-plan", plan)
    record = compile_checked(combined, doc, contract, provenance)
    plan["complete"] = True
    save("stage-plan", plan)
    save("combined-draft", combined)
    return record
