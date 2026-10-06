"""Bounded model protocol recovery; never fabricate missing evidence or coverage."""

import copy
import json

from jsonschema import Draft202012Validator


class ProtocolError(ValueError):
    def __init__(self, details):
        super().__init__("wire_schema_invalid")
        self.details = details


def proposal_check(wire, fmt, targets, *, defer_selectors=False):
    if defer_selectors:
        # Position alignment must see wrong numeric selectors before the strict compiler gate.
        fmt = copy.deepcopy(fmt)
        props = fmt["properties"]["contexts"]["items"]["properties"]
        for kind in ("actors", "collectives", "objects", "statements"):
            item = props[kind]["items"]["properties"]
            selector = (
                item["value"]["properties"]["source"] if kind == "statements" else item["mention"]
            )
            selector["properties"]["unit"] = {"type": "integer", "minimum": 0}
    errors = list(Draft202012Validator(fmt).iter_errors(wire))
    if errors:
        raise ProtocolError(
            [
                {
                    "path": list(e.path),
                    "rule": e.validator,
                    "expected": e.validator_value
                    if e.validator in {"enum", "required", "type"}
                    else None,
                }
                for e in errors[:16]
            ]
        )
    if sorted(r["unit"] for r in wire["coverage"]) != sorted(targets):
        raise ValueError("coverage_not_partition")


def infer_checked(calls, messages, stage, check, save, repairs=2):
    previous = None
    history = []
    for attempt in range(repairs + 1):
        name = stage if attempt == 0 else f"{stage}-protocol-repair-{attempt}"
        prompt = (
            messages
            if attempt == 0
            else messages
            + [
                {
                    "role": "user",
                    "content": json.dumps(
                        {
                            "任务": (
                                "上次输出未通过协议校验。根据同一原文重新输出完整合法数据实例，不猜证据，不删难例。"
                                "coverage必须完整逐项登记，语义无法判断用uncertain。"
                                "不得用空对象占位，不返回Schema定义，不省略重复内容。"
                                "不支持的语义结构请放入对应语境的unresolved并附原文单位和原因，不能强改为别的类型。"
                            ),
                            "失败类别": history[-1]["code"],
                            "具体错误": history[-1].get("details", []),
                            "上次结构化输出": previous,
                        },
                        ensure_ascii=False,
                    ),
                }
            ]
        )
        try:
            previous = calls.infer(prompt, name)
            save(name + "-parsed", previous)
            check(previous)
            save(stage + "-recovery", history)
            return previous
        except (ValueError, TypeError, KeyError, IndexError) as error:
            code = (
                str(error)
                if str(error)
                in {
                    "wire_schema_invalid",
                    "coverage_not_partition",
                    "transport_failure",
                    "provider_circuit_open",
                    "input_budget_exceeded",
                }
                else type(error).__name__
            )
            history.append(
                {
                    "attempt": attempt,
                    "stage": name,
                    "code": code,
                    "details": getattr(error, "details", []),
                }
            )
            save(stage + "-recovery", history)
            if getattr(calls, "halted", False) or code in {"input_budget_exceeded"}:
                raise
            if attempt == repairs:
                raise ValueError("protocol_recovery_exhausted") from error
    raise AssertionError("unreachable")
