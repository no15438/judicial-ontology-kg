"""Closed-choice alignment over immutable source and local reference inventories."""

import copy
import json

from judicial_kg.current.source import compact, locate


def get(value, path):
    for part in path:
        value = value[part]
    return value


def put(value, path, replacement):
    parent = get(value, path[:-1])
    parent[path[-1]] = replacement


def tasks_for(wire, doc, units):
    tasks, diagnostics = [], []

    def add(path, owner, replacements, state, instruction):
        if not replacements or len(replacements) > 254:
            diagnostics.append({"path": path, "owner": owner, "status": "no_bounded_candidates"})
            return
        tasks.append(
            {
                "id": f"a{len(tasks)}",
                "path": path,
                "owner": owner,
                "replacements": replacements,
                "options": {f"o{i}": str(value) for i, value in enumerate(replacements)},
                "state": state,
                "instruction": instruction,
            }
        )

    for ci, context in enumerate(wire.get("contexts", [])):
        if not isinstance(context, dict):
            continue
        scope = [i for i in context.get("units", []) if type(i) is int and 0 <= i < len(units)]
        background = [{"unit": i, "text": units[i]["text"]} for i in scope]
        prefix = ["contexts", ci]
        for kind in ("actors", "collectives", "objects", "statements"):
            for index, item in enumerate(context.get(kind, [])):
                owner = prefix + [kind, index]
                tail = ["value", "source"] if kind == "statements" else ["mention"]
                selector = None
                allowed = set(range(len(units))) if kind == "actors" else set(scope)
                try:
                    selector = get(item, tail)
                    locate(doc, units, selector, allowed)
                    # Exact unique existing selectors need no corrective call.
                    continue
                except (ValueError, KeyError, TypeError, IndexError):
                    pass
                if not isinstance(selector, dict) or not isinstance(selector.get("text"), str):
                    diagnostics.append(
                        {"path": owner + tail, "owner": owner, "status": "invalid_selector"}
                    )
                    continue
                needle, _ = compact(selector["text"])
                replacements = []
                if needle:
                    for uid in sorted(allowed):
                        haystack, _ = compact(units[uid]["text"])
                        if needle not in haystack:
                            continue
                        occurrence = 0
                        while True:
                            candidate = {
                                "unit": uid,
                                "text": selector["text"],
                                "occurrence": occurrence,
                            }
                            try:
                                locate(doc, units, candidate, allowed)
                            except ValueError:
                                break
                            replacements.append(candidate)
                            occurrence += 1
                state = {
                    "proposal": item,
                    "context": background,
                    "candidate_units": [
                        {"unit": i, "text": units[i]["text"]}
                        for i in sorted({r["unit"] for r in replacements})
                    ],
                }
                add(
                    owner + tail,
                    owner,
                    replacements,
                    state,
                    "选择该提及或值所对应的原文出现位置，不改写提及，不拼接文字。",
                )
        actors = context.get("actors", [])
        events = context.get("events", [])
        references = []
        for i, role in enumerate(context.get("roles", [])):
            if isinstance(role, dict):
                references.append(
                    (prefix + ["roles", i, "actor"], prefix + ["roles", i], role, actors)
                )
        for i, event in enumerate(events):
            if not isinstance(event, dict):
                continue
            for j, participant in enumerate(event.get("participants", [])):
                if (
                    isinstance(participant, dict)
                    and participant.get("target", {}).get("kind") == "actor"
                ):
                    references.append(
                        (
                            prefix + ["events", i, "participants", j, "target", "index"],
                            prefix + ["events", i],
                            {"event": event, "participant": participant},
                            actors,
                        )
                    )
        for i, statement in enumerate(context.get("statements", [])):
            if isinstance(statement, dict):
                pool = actors if statement.get("subject_kind") == "actor" else events
                references.append(
                    (
                        prefix + ["statements", i, "subject"],
                        prefix + ["statements", i],
                        statement,
                        pool,
                    )
                )
        for path, owner, proposal, pool in references:
            previous_count = len(tasks)
            add(
                path,
                owner,
                list(range(len(pool))),
                {
                    "proposal": proposal,
                    "context": background,
                    "inventory": [{"index": i, "candidate": v} for i, v in enumerate(pool)],
                },
                "核查当前下标的绑定。原绑定有依据则保持，即使其他对象也有相同角色。只有原绑定错误且新绑定唯一有依据才改；不确定选unknown。",
            )
            if len(tasks) > previous_count:
                tasks[-1]["options"] = {
                    f"o{i}": json.dumps(v, ensure_ascii=False) for i, v in enumerate(pool)
                }
    return tasks, diagnostics


def apply(wire, tasks, decisions):
    if set(decisions) != {t["id"] for t in tasks}:
        raise ValueError("alignment_decisions_mismatch")
    adapted, ledger, blocked = copy.deepcopy(wire), [], []
    for task in tasks:
        choice = decisions[task["id"]]
        before = get(wire, task["path"])
        if choice == "unknown":
            blocked.append(task["owner"])
            ledger.append(
                {"id": task["id"], "path": task["path"], "status": "unresolved", "before": before}
            )
            continue
        if choice not in task["options"]:
            raise ValueError("alignment_choice_outside_inventory")
        replacement = task["replacements"][int(choice[1:])]
        put(adapted, task["path"], copy.deepcopy(replacement))
        ledger.append(
            {
                "id": task["id"],
                "path": task["path"],
                "status": "unchanged" if before == replacement else "changed",
                "before": before,
                "after": replacement,
            }
        )
    return adapted, ledger, blocked
