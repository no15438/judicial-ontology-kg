"""V12/contract 0.3: explicit enclosing coverage and non-inherited assertion sources."""

import copy
import json

from jsonschema import Draft202012Validator

from judicial_kg.contracts import Contract, build_schema
from judicial_kg.extraction import actor_grounded, constrained, context_first, identity_first
from judicial_kg.extraction.anchored import GroundingError, source_units
from judicial_kg.sources.records import canonical_hash, evidence_span
from judicial_kg.validation import validate_record
from judicial_kg.validation_run import strict_json

CONTRACT_VERSION = "0.3.0"
NORMALIZER_VERSION = identity_first.NORMALIZER_VERSION
PROMPT_FILE = "prompts/qwen-extraction-v12.txt"
GENERATION_CONSTRAINT_VERSION = "explicit-enclosing-and-direct-scopes-v1"
GENERATION_CONSTRAINT_SCOPE = "validated_enclosing_partition_then_assertion_only_child_sources"
messages = identity_first.messages
response_format = constrained.response_format


def compiler_contract(contract):
    """Reuse unchanged selector/value compilation, then validate the new final contract."""
    if contract.ontology["version"] != CONTRACT_VERSION:
        raise ValueError("Scoped extraction requires contract 0.3.0")
    ontology = copy.deepcopy(contract.ontology)
    ontology["version"] = "0.2.0"
    schema = build_schema(ontology)
    return Contract(ontology, schema, Draft202012Validator(schema))


def full_schema(contract):
    schema = context_first.explicit_schema(compiler_contract(contract))
    context = schema["$defs"]["context"]
    enclosing = context["properties"].pop("evidence_units")
    context["required"].remove("evidence_units")
    context["properties"]["enclosing_units"] = enclosing
    context["properties"]["assertion_units"] = {**copy.deepcopy(enclosing), "minItems": 0}
    context["required"].extend(["enclosing_units", "assertion_units"])
    return schema


def draft_schema(contract):
    schema = full_schema(contract)
    schema["properties"]["actors"]["minItems"] = 1
    schema["properties"]["contexts"].update(minItems=1, maxItems=32)
    schema["$defs"]["actor"]["properties"]["mentions"]["maxItems"] = 1
    for kind in context_first.CHILDREN:
        schema["$defs"]["context"]["properties"][kind]["maxItems"] = 0
    return schema


def materialize(content, doc, contract, provenance):
    value = strict_json(content)
    context_first.check_schema(value, full_schema(contract))
    legacy = copy.deepcopy(value)
    for context in legacy["contexts"]:
        context["evidence_units"] = context.pop("enclosing_units")
        del context["assertion_units"]
    record = actor_grounded.materialize(
        json.dumps(legacy, ensure_ascii=False), doc, compiler_contract(contract), provenance
    )
    record.update(schema_version=CONTRACT_VERSION, ontology_version=CONTRACT_VERSION)
    units = {u["id"]: u for u in source_units(doc)}
    issues = []
    for index, (context, proposed) in enumerate(
        zip(record["contexts"], value["contexts"], strict=True)
    ):
        direct = []
        for position, identifier in enumerate(proposed["assertion_units"]):
            if identifier not in units:
                issues.append(
                    {
                        "code": "unknown_evidence_unit",
                        "path": f"/contexts/{index}/assertion_units/{position}",
                    }
                )
                continue
            unit = units[identifier]
            direct.append(evidence_span(doc, unit["field"], unit["start"], unit["end"]))
        context["assertion_evidence"] = direct
    if issues:
        raise GroundingError(issues)
    return record


def compile_checked(value, doc, contract, provenance):
    record = materialize(json.dumps(value, ensure_ascii=False), doc, contract, provenance)
    issues = validate_record(record, doc, contract)
    if issues:
        error = GroundingError(issues)
        error.code = "contract_rejected"
        raise error
    return record


def direct_context(context):
    return {**context, "evidence_units": context["assertion_units"]}


def body_schema(header, context, contract):
    return identity_first.body_schema(header, direct_context(context), compiler_contract(contract))


def body_messages(doc, prompt, catalog, context, schema):
    result = identity_first.body_messages(doc, prompt, catalog, direct_context(context), schema)
    payload = strict_json(result[-1]["content"])
    payload["frozen_context"].pop("evidence_units")
    result[-1]["content"] = json.dumps(payload, ensure_ascii=False)
    result[0]["content"] += (
        "\n子阶段只能使用assertion_units中的片段；enclosing_units只是包围范围，"
        "不是额外证据许可。被引用的子语境单独抽取，父阶段不继承其事实或提及。"
    )
    return result


def extract(doc, prompt, contract, provenance, infer, save, *, merge=None, on_header=None):
    schema = draft_schema(contract)
    header = strict_json(
        infer(messages(doc, prompt, schema), response_format(doc, schema), "header")
    )
    save("header-draft", header)
    context_first.check_schema(header, schema)
    header_record = compile_checked(header, doc, contract, provenance)
    if on_header is not None:
        on_header(header_record, doc, save)
    plan = {
        "header_sha256": canonical_hash(header),
        "source_units_total": len(source_units(doc)),
        "unassigned_source_unit_ids": [
            u["id"]
            for u in source_units(doc)
            if u["id"] not in {i for c in header["contexts"] for i in c["enclosing_units"]}
        ],
        "coverage_is_semantic_recall": False,
        "contexts": [
            {
                "context_id": c["id"],
                "enclosing_units": c["enclosing_units"],
                "assertion_units": c["assertion_units"],
                "status": "not_attempted",
            }
            for c in header["contexts"]
        ],
        "complete": False,
    }
    save("stage-plan", plan)
    combined = copy.deepcopy(header)
    for index, context in enumerate(header["contexts"]):
        row = plan["contexts"][index]
        if not context["assertion_units"]:
            # A validated container has child coverage, and no own assertions to generate.
            row["status"] = "structural_container_no_direct_source"
            save("stage-plan", plan)
            continue
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
            context_first.check_schema(body, schema)
            proposed = (
                merge(combined, index, body, save)
                if merge is not None
                else identity_first.merge_body(combined, index, body, namespace="v12")
            )
            # Namespacing is mechanical; no old candidate, context or source is rewritten.
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
