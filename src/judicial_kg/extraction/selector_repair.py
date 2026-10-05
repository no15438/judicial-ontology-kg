"""Bounded source-selector decisions without a prior accepted model baseline."""

import copy
import json
from collections import Counter

from jsonschema import Draft202012Validator

from judicial_kg.contracts import Contract
from judicial_kg.extraction import actor_grounded, constrained, contextual
from judicial_kg.extraction.anchored import GroundingError, source_units
from judicial_kg.extraction.draft import DraftError
from judicial_kg.extraction.mention_repair import build_plan as mention_plan
from judicial_kg.extraction.mention_repair import occurrences, selector
from judicial_kg.extraction.replay import load_saved_run
from judicial_kg.extraction.run import completion_content, load_config, run_extraction
from judicial_kg.sources.development import write_json
from judicial_kg.sources.records import canonical_hash, sha256
from judicial_kg.validation_run import strict_json, validate_batch

VERSION = "qwen-selector-repair-v1"
CONSTRAINED_VERSION = "qwen-selector-repair-v2"
PROMPT_FILE = "prompts/qwen-selector-repair-v1.txt"


def at(value, path):
    for key in path:
        value = value[key]
    return value


def build_plan(doc, draft, contract):
    # Reuse the existing exact-position mention catalog; no baseline hints are supplied.
    base = mention_plan(doc, draft, {"mentions": []}, contract)
    items = []
    for item in base["selector_items"]:
        items.append(
            {
                **{k: v for k, v in item.items() if k != "duplicate_targets"},
                "path": ["mentions", item["mention_index"], "evidence"],
                "kind_path": ["mentions", item["mention_index"], "kind"],
                "target_kind": "mention",
            }
        )
    units = source_units(doc)
    for ci, context in enumerate(draft["contexts"]):
        scope = [u for u in units if u["id"] in context["evidence_units"]]
        if len(scope) != len(context["evidence_units"]):
            raise ValueError("Unknown context source units cannot be repaired as value selectors")
        properties = [
            (s, ["contexts", ci, "statements", si, "value", "source"], None)
            for si, s in enumerate(context["statements"])
        ]
        properties.extend(
            (
                s,
                ["contexts", ci, "events", ei, "statements", si, "value", "source"],
                {k: v for k, v in event.items() if k != "statements"},
            )
            for ei, event in enumerate(context["events"])
            for si, s in enumerate(event["statements"])
        )
        for prop, path, event_info in properties:
            e = prop["value"]["source"]
            matches = occurrences(scope, e["text"])
            local = [p for p in matches if p["unit_id"] == e["unit_id"]]
            nth = e["occurrence"]
            valid = len(local) == 1 if nth is None else 0 <= nth < len(local)
            if valid:
                continue
            choices = local or matches
            items.append(
                {
                    "item_id": f"v{len(items)}",
                    "target_kind": "statement_value",
                    "path": path,
                    "kind_path": None,
                    "original_selector": e,
                    "context_id": context["id"],
                    "scope_units": context["evidence_units"],
                    "predicate": prop["predicate"],
                    "statement": prop,
                    "event": event_info,
                    "original_unit": None,
                    "choices": [{"choice_id": f"p{i}", **p} for i, p in enumerate(choices)],
                }
            )
    return {
        "document_id": doc["document_id"],
        "draft_sha256": canonical_hash(draft),
        "source_units": base["source_units"],
        "actors": draft["actors"],
        "contexts": [
            {k: v for k, v in c.items() if k not in {"events", "statements", "roles"}}
            for c in draft["contexts"]
        ],
        "grounded_mentions": base["grounded_mentions"],
        "selector_items": items,
        "baseline_used": False,
    }


def response_schema():
    optional_string = {"type": ["string", "null"], "minLength": 1, "maxLength": 64}
    props = {
        "item_id": {"type": "string", "minLength": 1},
        "action": {"enum": ["locate", "local_span", "uncertain"]},
        "choice_id": optional_string,
        "local_text": optional_string,
        "mention_kind": {"enum": [None, "name", "pronoun", "description"]},
        "reason": {"type": "string", "minLength": 1, "maxLength": 300},
    }
    return {
        "type": "object",
        "properties": {
            "selector_choices": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": props,
                    "required": list(props),
                    "additionalProperties": False,
                },
            }
        },
        "required": ["selector_choices"],
        "additionalProperties": False,
    }


def stage_choices(draft, plan, choices):
    if canonical_hash(draft) != plan["draft_sha256"]:
        raise ValueError("Draft differs from frozen repair plan")
    if list(Draft202012Validator(response_schema()).iter_errors(choices)):
        raise DraftError("selector_repair_schema_invalid")
    decisions = choices["selector_choices"]
    ids = [x["item_id"] for x in decisions]
    if len(set(ids)) != len(ids) or set(ids) != {x["item_id"] for x in plan["selector_items"]}:
        raise DraftError("selector_repair_item_coverage_mismatch")
    by_id = {x["item_id"]: x for x in decisions}
    patched, counts, issues = copy.deepcopy(draft), Counter(), []
    for item in plan["selector_items"]:
        d = by_id[item["item_id"]]
        counts[d["action"]] += 1
        if d["action"] == "uncertain":
            if any(d[k] is not None for k in ("choice_id", "local_text", "mention_kind")):
                raise DraftError("selector_repair_uncertain_has_patch")
            issues.append(
                {
                    "code": "selector_repair_uncertain",
                    "path": "/" + "/".join(map(str, item["path"])),
                }
            )
            continue
        if d["action"] == "locate":
            options = [p for p in item["choices"] if p["choice_id"] == d["choice_id"]]
            if len(options) != 1 or d["local_text"] is not None or d["mention_kind"] is not None:
                raise DraftError("selector_repair_choice_outside_catalog")
            selected = selector(options[0])
        else:
            unit = item["original_unit"]
            if (
                item["target_kind"] != "mention"
                or unit is None
                or d["choice_id"] is not None
                or d["mention_kind"] is None
                or not isinstance(d["local_text"], str)
            ):
                raise DraftError("selector_repair_local_span_invalid")
            positions = occurrences([unit], d["local_text"])
            if len(positions) != 1:
                raise DraftError("selector_repair_local_text_not_unique")
            selected = selector(positions[0])
            parent = at(patched, item["kind_path"][:-1])
            parent[item["kind_path"][-1]] = d["mention_kind"]
        if item["target_kind"] == "statement_value":
            if (
                selected["text"] != item["original_selector"]["text"]
                or selected["unit_id"] not in item["scope_units"]
            ):
                raise DraftError("selector_repair_value_scope_or_text_changed")
        parent = at(patched, item["path"][:-1])
        parent[item["path"][-1]] = selected
    # Verify all semantic fields are immutable; only enumerated selectors and
    # explicitly reviewed mention kinds may change. Nothing can be added/deleted.
    unchanged = copy.deepcopy(patched)
    for item in plan["selector_items"]:
        for path in (item["path"], item["kind_path"]):
            if path is not None:
                at(unchanged, path[:-1])[path[-1]] = copy.deepcopy(at(draft, path))
    assert unchanged == draft
    return patched, dict(counts), issues


def apply_choices(draft, plan, choices):
    patched, counts, issues = stage_choices(draft, plan, choices)
    if issues:
        raise GroundingError(issues)
    return patched, counts


class SelectorRepair:
    CONTRACT_VERSION = "0.2.0"
    NORMALIZER_VERSION = contextual.NORMALIZER_VERSION
    PROMPT_FILE = PROMPT_FILE
    RUN_KIND = "local_qwen_bounded_selector_repair_not_gold"

    def __init__(self, drafts, plans, ids, lineage):
        self.drafts, self.plans, self.selection_ids, self.lineage = drafts, plans, ids, lineage
        self.decision_counts = {}

    def draft_schema(self, contract):
        return response_schema()

    def check_selection(self, docs, config):
        if config["version"] != VERSION or [d["document_id"] for d in docs] != self.selection_ids:
            raise ValueError("Repair selection must exactly retain frozen target IDs")

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
        try:
            choices = strict_json(content)
        except (ValueError, TypeError):
            raise DraftError("selector_repair_invalid_json") from None
        patched, counts = apply_choices(
            self.drafts[doc["document_id"]], self.plans[doc["document_id"]], choices
        )
        self.decision_counts[doc["document_id"]] = counts
        write_json(
            self.output / ("patched-" + sha256(doc["document_id"].encode())[:16] + ".json"), patched
        )
        return contextual.materialize(
            json.dumps(patched, ensure_ascii=False), doc, contract, provenance
        )


class ConstrainedSelectorRepair(SelectorRepair):
    PROMPT_FILE = "prompts/qwen-selector-repair-v2.txt"
    GENERATION_CONSTRAINT_VERSION = "frozen-selector-catalog-and-bounded-reasons-v1"
    GENERATION_CONSTRAINT_SCOPE = "selector_item_ids_count_choices_and_reason_length"

    def draft_schema(self, contract):
        schema = response_schema()
        schema["properties"]["selector_choices"]["items"]["properties"]["reason"]["maxLength"] = 80
        return schema

    def bound_schema(self, doc):
        schema = self.draft_schema(None)
        items = self.plans[doc["document_id"]]["selector_items"]
        array = schema["properties"]["selector_choices"]
        array.update(minItems=len(items), maxItems=len(items))
        props = array["items"]["properties"]
        props["item_id"]["enum"] = [i["item_id"] for i in items]
        props["choice_id"]["enum"] = sorted(
            {p["choice_id"] for item in items for p in item["choices"]}
        ) + [None]
        return schema

    def response_format(self, doc, schema):
        return constrained.response_format(doc, self.bound_schema(doc))

    def check_selection(self, docs, config):
        if (
            config["version"] != CONSTRAINED_VERSION
            or [d["document_id"] for d in docs] != self.selection_ids
        ):
            raise ValueError("Repair selection must exactly retain frozen target IDs")

    def messages(self, doc, prompt, schema):
        return super().messages(doc, prompt, self.bound_schema(doc))

    def materialize(self, content, doc, contract, provenance):
        try:
            choices = strict_json(content)
        except (ValueError, TypeError):
            raise DraftError("selector_repair_invalid_json") from None
        if list(Draft202012Validator(self.bound_schema(doc)).iter_errors(choices)):
            raise DraftError("selector_repair_schema_invalid")
        return super().materialize(content, doc, contract, provenance)


def prepare_repair(root, document_paths, specification, version=VERSION):
    if version not in {VERSION, CONSTRAINED_VERSION}:
        raise ValueError("Unsupported selector repair version")
    docs = [d for p in document_paths for d in strict_json(p.read_bytes())]
    contract = Contract.load(root, "0.2.0")
    validate_batch(docs, [], contract)
    all_docs = {d["document_id"]: d for d in docs}
    drafts, plans, ids, lineage = {}, {}, [], []
    specs_raw = specification.read_bytes()
    for spec in strict_json(specs_raw):
        raw, original, protocol, _, source_docs, _, selected, calls, responses = load_saved_run(
            root, [root / p for p in spec["documents"]], root / spec["source_run"]
        )
        if original["config"]["version"] not in {
            "qwen-extraction-v6",
            "qwen-extraction-v7",
            "qwen-extraction-v8",
            "qwen-extraction-v9",
        }:
            raise ValueError("Selector repair requires a completed v6-v9 source run")
        if {d["document_id"]: d for d in source_docs} != all_docs:
            raise ValueError("Source runs and repair must retain the same full denominator")
        targets = spec["document_ids"]
        if not targets or len(set(targets)) != len(targets):
            raise ValueError("Invalid selector repair targets")
        outcomes = {o["document_id"]: o for o in original["selected_outcomes"]}
        if any(
            k not in outcomes
            or outcomes[k]["status"] not in {"draft_grounding_failed", "contract_rejected"}
            for k in targets
        ):
            raise ValueError("Repair targets must be saved rejected complete drafts")
        for doc, _call, response in zip(selected, calls, responses, strict=True):
            key = doc["document_id"]
            if key not in targets:
                continue
            if key in ids:
                raise ValueError("Duplicate document target across source runs")
            content, _ = completion_content(response, original["config"]["model"])
            draft = (
                actor_grounded.unpack_draft(content, contract)
                if getattr(protocol, "unpack_draft", None) is actor_grounded.unpack_draft
                else contextual.unpack_draft(content)
            )
            drafts[key] = draft
            plans[key] = build_plan(doc, draft, contract)
            if not plans[key]["selector_items"]:
                raise ValueError("No supported selector error in selected rejected draft")
            ids.append(key)
        lineage.append(
            {
                "source_run_id": original["run_id"],
                "source_report_sha256": sha256(raw),
                "source_protocol": original["config"]["version"],
                "target_outcomes": [outcomes[k] for k in targets],
                "response_sha256": [
                    c["response_sha256"]
                    for d, c in zip(selected, calls, strict=True)
                    if d["document_id"] in targets
                ],
            }
        )
    factory = ConstrainedSelectorRepair if version == CONSTRAINED_VERSION else SelectorRepair
    return factory(
        drafts,
        plans,
        ids,
        {
            "specification_sha256": sha256(specs_raw),
            "sources": lineage,
            "draft_representation": (
                "v6 flat draft; v7-v9 nesting flattened and matching redundant types removed; "
                "original responses remain immutable"
                if any(
                    s["source_protocol"] in {"qwen-extraction-v8", "qwen-extraction-v9"}
                    for s in lineage
                )
                else (
                    "v6 flat draft; v7 nesting flattened and matching redundant types removed; "
                    "original responses remain immutable"
                )
            ),
            "baseline_used": False,
            "plan_counts": {
                k: dict(Counter(i["target_kind"] for i in p["selector_items"]))
                for k, p in plans.items()
            },
        },
    )


def run_repair(root, document_paths, specification, config, output, client=None):
    protocol = prepare_repair(root, document_paths, specification, load_config(config)["version"])
    return run_extraction(root, document_paths, config, output, client, protocol)


def replay_repair(root, document_paths, specification, saved_repair, output):
    from judicial_kg.extraction.repair_replay import replay_prepared

    version = strict_json((saved_repair / "report.json").read_bytes())["config"]["version"]
    protocol = prepare_repair(root, document_paths, specification, version)
    return replay_prepared(root, document_paths, saved_repair, output, protocol, version)
