"""Bounded model-selected mention repairs; immutable source runs and strict grounding."""

import copy
import json
import re
from collections import Counter
from pathlib import Path

from jsonschema import Draft202012Validator

from judicial_kg.contracts import Contract
from judicial_kg.extraction import contextual
from judicial_kg.extraction.anchored import GroundingError, source_units
from judicial_kg.extraction.draft import DraftError
from judicial_kg.extraction.replay import load_saved_run
from judicial_kg.extraction.run import completion_content, run_extraction
from judicial_kg.sources.development import write_json
from judicial_kg.sources.records import canonical_hash, sha256
from judicial_kg.validation_run import strict_json, validate_batch

PROMPT_FILE = "prompts/qwen-mention-repair-v2.txt"
VERSION = "qwen-mention-repair-v2"


def occurrences(units, text):
    return [
        {
            "unit_id": u["id"],
            "text": text,
            "occurrence": i,
            "field": u["field"],
            "start": u["start"] + m.start(),
            "end": u["start"] + m.start() + len(text),
        }
        for u in units
        for i, m in enumerate(re.finditer("(?=" + re.escape(text) + ")", u["text"]))
    ]


def selector(position):
    return {k: position[k] for k in ("unit_id", "text", "occurrence")}


def build_plan(doc, draft, baseline, contract):
    errors = list(Draft202012Validator(contextual.draft_schema(contract)).iter_errors(draft))
    if errors:
        raise ValueError("Repair requires a schema-valid contextual draft")
    units = source_units(doc)
    grounded, invalid = [], []
    for index, mention in enumerate(draft["mentions"]):
        e = mention["evidence"]
        all_matches = occurrences(units, e["text"])
        local = [p for p in all_matches if p["unit_id"] == e["unit_id"]]
        nth = e["occurrence"]
        found = (
            local[0]
            if nth is None and len(local) == 1
            else (local[nth] if type(nth) is int and 0 <= nth < len(local) else None)
        )
        repeated = found is not None and any(
            x["actor_id"] == mention["actor_id"]
            and x["field"] == found["field"]
            and x["start"] == found["start"]
            and x["end"] == found["end"]
            for x in grounded
        )
        if found and not repeated:
            grounded.append({"mention_id": mention["id"], "actor_id": mention["actor_id"], **found})
        else:
            # Ambiguity within the supplied unit is resolved within that unit.
            # A missing quote may be selected anywhere in the actual source;
            # proximity never chooses or ranks a winner automatically.
            choices = local if local and not repeated else all_matches
            invalid.append(
                {
                    "item_id": f"s{index}",
                    "mention_index": index,
                    "mention_id": mention["id"],
                    "actor_id": mention["actor_id"],
                    "original_selector": e,
                    "failure_kind": "duplicate_position" if repeated else "unresolved_position",
                    "choices": [{"choice_id": f"p{i}", **p} for i, p in enumerate(choices)],
                }
            )
    for item in invalid:
        item["choices"] = [
            p
            for p in item["choices"]
            if not any(
                g["actor_id"] == item["actor_id"]
                and g["field"] == p["field"]
                and g["start"] == p["start"]
                and g["end"] == p["end"]
                for g in grounded
            )
        ]
        item["duplicate_targets"] = [
            g
            for g in grounded
            if g["actor_id"] == item["actor_id"] and g["text"] == item["original_selector"]["text"]
        ]
    missing = []
    for mention in baseline["mentions"]:
        e = mention["evidence"]
        if any(
            p["field"] == e["field"] and p["start"] < e["end"] and p["end"] > e["start"]
            for p in grounded
        ):
            continue
        exact = [
            p
            for p in occurrences(units, e["quote"])
            if (p["field"], p["start"], p["end"]) == (e["field"], e["start"], e["end"])
        ]
        missing.append(
            {
                "item_id": f"h{len(missing)}",
                "previous_mention_id": mention["id"],
                "source_position": exact[0] if len(exact) == 1 else None,
                "reference_status": "previous_model_candidate_not_gold",
            }
        )
    return {
        "document_id": doc["document_id"],
        "draft_sha256": canonical_hash(draft),
        "baseline_sha256": canonical_hash(baseline),
        "source_units": [{k: u[k] for k in ("id", "field", "text")} for u in units],
        "actors": draft["actors"],
        "grounded_mentions": grounded,
        "selector_items": invalid,
        "coverage_items": missing,
    }


def response_schema(contract):
    nullable = lambda values: {"enum": [None, *values]}  # noqa: E731

    def obj(properties):
        return {
            "type": "object",
            "properties": properties,
            "required": list(properties),
            "additionalProperties": False,
        }

    reason = {"type": "string", "minLength": 1, "maxLength": 300}
    string = {"type": "string", "minLength": 1}
    optional_string = {"anyOf": [string, {"type": "null"}]}
    return obj(
        {
            "selector_choices": {
                "type": "array",
                "items": obj(
                    {
                        "item_id": string,
                        "action": {"enum": ["locate", "duplicate", "uncertain"]},
                        "choice_id": optional_string,
                        "duplicate_of": optional_string,
                        "reason": reason,
                    }
                ),
            },
            "coverage_choices": {
                "type": "array",
                "items": obj(
                    {
                        "item_id": string,
                        "action": {"enum": ["add", "covered", "not_mention", "uncertain"]},
                        "actor_id": optional_string,
                        "actor_type": nullable(contract.ontology["actor_types"]),
                        "mention_kind": nullable(contract.ontology["mention_kinds"]),
                        "reason": reason,
                    }
                ),
            },
        }
    )


def apply_choices(draft, plan, choices, contract):
    if canonical_hash(draft) != plan["draft_sha256"]:
        raise ValueError("Draft changed after repair plan creation")
    if list(Draft202012Validator(response_schema(contract)).iter_errors(choices)):
        raise DraftError("repair_schema_invalid")
    patched = copy.deepcopy(draft)
    issues, actions = [], Counter()
    existing_ids = set()

    def collect(value):
        if isinstance(value, dict):
            if "id" in value:
                existing_ids.add(value["id"])
            for v in value.values():
                collect(v)
        elif isinstance(value, list):
            for v in value:
                collect(v)

    collect(draft)

    def fresh(prefix):
        index = 0
        while f"{prefix}{index}" in existing_ids:
            index += 1
        identifier = f"{prefix}{index}"
        existing_ids.add(identifier)
        return identifier

    def bind(items, decisions):
        ids = [d["item_id"] for d in decisions]
        if len(set(ids)) != len(ids) or set(ids) != {i["item_id"] for i in items}:
            raise DraftError("repair_item_coverage_mismatch")
        return {d["item_id"]: d for d in decisions}

    removed_indexes = set()
    patched_positions = list(plan["grounded_mentions"])
    selected = bind(plan["selector_items"], choices["selector_choices"])
    for item in plan["selector_items"]:
        decision = selected[item["item_id"]]
        if decision["action"] == "duplicate":
            targets = [
                g for g in item["duplicate_targets"] if g["mention_id"] == decision["duplicate_of"]
            ]
            if len(targets) != 1 or decision["choice_id"] is not None:
                raise DraftError("repair_duplicate_target_invalid")
            removed_indexes.add(item["mention_index"])
            actions["explicit_duplicate_removed"] += 1
            continue
        if decision["duplicate_of"] is not None:
            raise DraftError("repair_unused_duplicate_target")
        if decision["action"] == "uncertain":
            if decision["choice_id"] is not None:
                raise DraftError("repair_unused_choice")
            issues.append({"code": "selector_unresolved", "path": item["item_id"]})
            continue
        if decision["choice_id"] is None:
            raise DraftError("repair_location_choice_required")
        matches = [p for p in item["choices"] if p["choice_id"] == decision["choice_id"]]
        if len(matches) != 1:
            raise DraftError("repair_choice_outside_catalog")
        patched["mentions"][item["mention_index"]]["evidence"] = selector(matches[0])
        patched_positions.append(matches[0])
        actions["selector_changed"] += 1
    patched["mentions"] = [m for i, m in enumerate(patched["mentions"]) if i not in removed_indexes]
    selected = bind(plan["coverage_items"], choices["coverage_choices"])
    actor_ids = {a["id"] for a in draft["actors"]}
    for item in plan["coverage_items"]:
        decision = selected[item["item_id"]]
        action = decision["action"]
        position = item["source_position"]
        covered = position is not None and any(
            p["field"] == position["field"]
            and p["start"] < position["end"]
            and p["end"] > position["start"]
            for p in patched_positions
        )
        actions["coverage_" + action] += 1
        if action != "add":
            if any(decision[k] is not None for k in ("actor_id", "actor_type", "mention_kind")):
                raise DraftError("repair_unused_assignment")
            if action == "covered" and not covered:
                raise DraftError("repair_coverage_not_actually_covered")
            if action == "uncertain":
                issues.append({"code": "coverage_unresolved", "path": item["item_id"]})
            continue
        if covered:
            raise DraftError("repair_add_overlaps_existing")
        if item["source_position"] is None or decision["mention_kind"] is None:
            raise DraftError("repair_missing_source_or_kind")
        actor_id = decision["actor_id"]
        if actor_id is None:
            if decision["actor_type"] is None:
                raise DraftError("repair_new_actor_type_required")
            actor_id = fresh("repair_a")
            patched["actors"].append({"id": actor_id, "actor_type": decision["actor_type"]})
        elif actor_id not in actor_ids or decision["actor_type"] is not None:
            raise DraftError("repair_actor_assignment_invalid")
        patched_positions.append(position)
        patched["mentions"].append(
            {
                "id": fresh("repair_m"),
                "actor_id": actor_id,
                "kind": decision["mention_kind"],
                "evidence": selector(item["source_position"]),
            }
        )
    if issues:
        raise GroundingError(issues)
    # No model patch can alter existing actor identity/type, context, role,
    # event, statement, polarity or literal quote text.
    assert patched["contexts"] == draft["contexts"]
    assert patched["actors"][: len(draft["actors"])] == draft["actors"]
    return patched, dict(actions)


class MentionRepair:
    CONTRACT_VERSION = "0.2.0"
    PROMPT_FILE = PROMPT_FILE
    NORMALIZER_VERSION = contextual.NORMALIZER_VERSION

    def __init__(self, drafts, plans, selected_ids, lineage):
        self.drafts, self.plans = drafts, plans
        self.selected_ids, self.lineage = selected_ids, lineage
        self.decision_counts = {}

    def draft_schema(self, contract):
        return response_schema(contract)

    def check_selection(self, docs, config):
        if config["version"] != VERSION or [d["document_id"] for d in docs] != self.selected_ids:
            raise ValueError("Repair must retain the original selection and repair protocol")

    def save_plan(self, output):
        self.output = output
        write_json(output / "repair-plans.json", self.plans)
        write_json(output / "original-drafts.json", self.drafts)

    def messages(self, doc, prompt, schema):
        return [
            {"role": "system", "content": prompt + "\nJSON Schema:\n" + json.dumps(schema)},
            {
                "role": "user",
                "content": json.dumps(self.plans[doc["document_id"]], ensure_ascii=False),
            },
        ]

    def materialize(self, content, doc, contract, provenance):
        doc_id = doc["document_id"]
        try:
            choices = strict_json(content)
        except (ValueError, TypeError):
            raise DraftError("repair_invalid_json") from None
        patched, counts = apply_choices(self.drafts[doc_id], self.plans[doc_id], choices, contract)
        self.decision_counts[doc_id] = counts
        write_json(self.output / ("patched-" + sha256(doc_id.encode())[:16] + ".json"), patched)
        return contextual.materialize(
            json.dumps(patched, ensure_ascii=False), doc, contract, provenance
        )


def prepare_repair(root, document_paths, source_run, baseline_path):
    (raw, original, _, contract, docs, _, selected, _, responses) = load_saved_run(
        root, document_paths, source_run
    )
    if original["config"]["version"] != "qwen-extraction-v6":
        raise ValueError("Mention repair requires an immutable completed v6 source run")
    baseline_raw = baseline_path.read_bytes()
    baseline_report_raw = (baseline_path.parent / "report.json").read_bytes()
    baseline_report = strict_json(baseline_report_raw)
    if not baseline_report["completed"] or baseline_report["outputs_sha256"][
        baseline_path.name
    ] != sha256(baseline_raw):
        raise ValueError("Baseline candidates do not match their completed saved run")
    baseline_validation = validate_batch(
        docs, baseline_raw.decode().splitlines(), Contract.load(root, "0.1.0")
    )
    if baseline_validation["summary"]["attempts_rejected"]:
        raise ValueError("Baseline must contain source-validated model candidates")
    baseline = {r["document_id"]: r for r in baseline_validation["accepted_records"]}
    drafts, plans = {}, {}
    for doc, response in zip(selected, responses, strict=True):
        content, _ = completion_content(response, original["config"]["model"])
        draft = strict_json(content)
        doc_id = doc["document_id"]
        if doc_id not in baseline:
            raise ValueError("Every selected document requires a matched baseline candidate")
        drafts[doc_id] = draft
        plans[doc_id] = build_plan(doc, draft, baseline[doc_id], contract)
    lineage = {
        "source_run_id": original["run_id"],
        "source_report_sha256": sha256(raw),
        "source_model": original["config"]["model"],
        "baseline_candidates_sha256": sha256(baseline_raw),
        "baseline_report_sha256": sha256(baseline_report_raw),
        "original_document_status_counts": original["document_status_counts"],
        "plan_counts": {
            k: {n: len(v[n]) for n in ("selector_items", "coverage_items")}
            for k, v in plans.items()
        },
        "previous_candidates_are_gold": False,
    }
    return MentionRepair(drafts, plans, [d["document_id"] for d in selected], lineage)


def run_repair(root: Path, document_paths, source_run, baseline, config, output, client=None):
    protocol = prepare_repair(root, document_paths, source_run, baseline)
    return run_extraction(root, document_paths, config, output, client, protocol)
