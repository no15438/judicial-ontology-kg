"""V42: bounded schema-echo correction and explicit unresolved-binding audit."""

import copy

from judicial_kg.extraction import source_positions
from judicial_kg.extraction.draft import DraftError
from judicial_kg.validation_run import strict_json

CONTRACT_VERSION = source_positions.CONTRACT_VERSION
NORMALIZER_VERSION = source_positions.NORMALIZER_VERSION
PROMPT_FILE = source_positions.PROMPT_FILE
GENERATION_CONSTRAINT_VERSION = "schema-echo-correction-and-unresolved-audit-v1"
GENERATION_CONSTRAINT_SCOPE = "format-correction-only-no-invented-identities"
draft_schema = source_positions.draft_schema
response_format = source_positions.response_format


def is_schema_echo(raw, schema):
    if raw == schema:
        return True
    # Forms discovery has a single data key; some echoes alter Schema limits.
    return (
        isinstance(raw, dict)
        and set(schema.get("properties", {})) == {"forms"}
        and raw.get("type") == "object"
        and isinstance(raw.get("properties"), dict)
        and "forms" in raw["properties"]
        and set(raw)
        <= {
            "type",
            "properties",
            "required",
            "additionalProperties",
            "$schema",
            "title",
            "description",
        }
    )


class ResponseGuard:
    def __init__(self, infer, save):
        self.infer, self.save = infer, save
        self.repairs = 0

    def call(self, messages, fmt, stage):
        content = self.infer(messages, fmt, stage)
        raw = strict_json(content)
        if is_schema_echo(raw, fmt["schema"]):
            self.save(stage + "-schema-echo-original", raw)
            if self.repairs >= 2:
                raise DraftError("schema_echo_repair_budget_exhausted")
            self.repairs += 1
            request = copy.deepcopy(messages)
            request[0]["content"] += (
                "\n上次响应错误地返回了Schema定义。请重新依据本次原文输出符合Schema的数据实例，"
                "不要输出type/properties/required等Schema定义。不得补造原文或主体身份。"
            )
            content = self.infer(request, fmt, stage + "-repair-schema-1")
            raw = strict_json(content)
            self.save(
                stage + "-schema-echo-receipt",
                {
                    "repair_calls_used": self.repairs,
                    "repair_calls_limit": 2,
                    "schema_echo_remains": is_schema_echo(raw, fmt["schema"]),
                    "semantic_verified": False,
                },
            )
            if is_schema_echo(raw, fmt["schema"]):
                raise DraftError("schema_echo_unresolved")
        if stage.endswith("-mentions") and isinstance(raw, dict):
            mentions = raw.get("mentions")
            if isinstance(mentions, dict):
                empty = [key for key, value in mentions.items() if value == {}]
                if empty:
                    payload = strict_json(messages[-1]["content"])
                    catalog = payload.get("catalog", {})
                    self.save(
                        stage + "-unresolved-bindings",
                        {
                            "status": "unresolved_not_accepted",
                            "span_ids": empty,
                            "source_positions": [
                                e for e in catalog.get("entries", []) if e.get("span_id") in empty
                            ],
                            "proposal_decisions": raw.get("proposal_decisions"),
                            "source_units": payload.get("source_units", []),
                            "original_response": raw,
                            "semantic_verified": False,
                        },
                    )
                    raise DraftError("mention_binding_unresolved")
        return content


def extract(doc, prompt, contract, provenance, infer, save):
    guard = ResponseGuard(infer, save)
    return source_positions.extract(doc, prompt, contract, provenance, guard.call, save)
