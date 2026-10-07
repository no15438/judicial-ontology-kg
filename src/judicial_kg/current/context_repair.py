"""Repair only failing contexts; source units and unaffected contexts remain immutable."""

import copy
import json

from jsonschema import Draft202012Validator

from judicial_kg.current.protocol import PROMPT, obj, schema
from judicial_kg.current.recovery import ProtocolError, defer_selector_scope, infer_checked

GUIDANCE = """只修复指定局部语境，返回完整context实例，不能输出其他语境或coverage。
必须保留units及无错候选；同步修正该语境内数组下标。无法修复的原命题逐项登记unresolved及原文依据。
party_claim/party_defense的speakers不得为空，可用背景的诉称/辩称引导句确立持续说话者。
若无法确定归属，仅保留unknown语境与unresolved；不产生确定归属的角色、事件、陈述。
法院语境speakers是作出认定的法院，不是认定涉及的人。未知归属可以为空。
合议庭、双方当事人、各方当事人只能放collectives，不可降级unknown actor绕过冲突。
没有约定利息不是利率0，否认欠款不是金额0。有数值才能使用对应数值谓词；纯文本可用有据的other或unresolved。
每个事件至少有一个明确主体或集合参与者，不能编造参与者；无依据则原事件登记unresolved。
事件关联对象时，其units必须包含对象提及与事件关系的直接依据。若不支持对象关联则该关联登记unresolved。
集合成员未知用unknown和空members；仅有部分明确成员用partial；explicit要求多个成员且完整性有原文依据。"""


def repair_contexts(wire, failures, contract, payload, calls, stage, save, *, align_enabled):
    adapted = copy.deepcopy(wire)
    affected = {}
    for failure in failures:
        try:
            ci = int(failure["path"].split("/contexts/")[1].split("/")[0])
        except (KeyError, IndexError, ValueError):
            raise ValueError("unlocatable_contract_failure") from None
        affected.setdefault(ci, []).append(failure)
    all_units = payload["target_units"] + payload["background_units"]
    for ci, errors in sorted(affected.items()):
        context = wire["contexts"][ci]
        ids = context["units"]
        fmt = schema(contract, ids, [u["unit"] for u in all_units])
        check_fmt = defer_selector_scope(fmt) if align_enabled else fmt
        envelope = obj(context=check_fmt["properties"]["contexts"]["items"])
        diagnostics = []
        for error in errors:
            detail = {"failure": error}
            path = error["path"].split("/contexts/")[1].split("/")[1:]
            if len(path) == 2 and path[0] == "events":
                event = context["events"][int(path[1])]
                detail["event"] = event
                detail["object_anchors"] = [
                    context["objects"][i]["mention"]
                    for i in event["objects"]
                    if type(i) is int and 0 <= i < len(context["objects"])
                ]
            diagnostics.append(detail)

        def check(value, envelope=envelope, ids=ids):
            violations = list(Draft202012Validator(envelope).iter_errors(value))
            if violations:
                raise ProtocolError(
                    [
                        {"path": list(e.path), "rule": e.validator, "expected": e.validator_value}
                        for e in violations[:16]
                    ]
                )
            if sorted(value["context"]["units"]) != sorted(ids):
                raise ProtocolError(
                    [
                        {
                            "path": ["context", "units"],
                            "rule": "original_context_units",
                            "expected": ids,
                        }
                    ]
                )

        result = infer_checked(
            calls,
            [
                {
                    "role": "system",
                    "content": PROMPT
                    + "\n"
                    + GUIDANCE
                    + "\nSchema:\n"
                    + json.dumps(envelope, ensure_ascii=False),
                },
                {
                    "role": "user",
                    "content": json.dumps(
                        {
                            "target_units": [u for u in all_units if u["unit"] in ids],
                            "background_units": [u for u in all_units if u["unit"] not in ids],
                            "context": context,
                            "diagnostics": diagnostics,
                            "predicate_value_kinds": contract.ontology["predicate_value_kinds"],
                        },
                        ensure_ascii=False,
                    ),
                },
            ],
            f"{stage}-context-{ci:03}",
            check,
            save,
            repair_instructions=GUIDANCE,
        )
        adapted["contexts"][ci] = result["context"]
    save(stage + "-proposal", adapted)
    return adapted
