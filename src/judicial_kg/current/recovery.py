"""Bounded model protocol recovery; never fabricate missing evidence or coverage."""

import json

from judicial_kg.current.compiler import Compiler


def proposal_check(wire, fmt, targets):
    Compiler.check(wire, fmt)
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
                            ),
                            "失败类别": history[-1]["code"],
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
            history.append({"attempt": attempt, "stage": name, "code": code})
            save(stage + "-recovery", history)
            if getattr(calls, "halted", False) or code in {"input_budget_exceeded"}:
                raise
            if attempt == repairs:
                raise ValueError("protocol_recovery_exhausted") from error
    raise AssertionError("unreachable")
