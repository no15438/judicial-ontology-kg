"""Review staged local assertions without inventing text or crossing contexts."""

import copy
import json
from uuid import uuid4

from jsonschema import Draft202012Validator

from judicial_kg.contracts import Contract
from judicial_kg.extraction import attribution_repair, constrained, contextual, selector_repair
from judicial_kg.extraction.anchored import GroundingError, source_units
from judicial_kg.extraction.draft import DraftError
from judicial_kg.extraction.mention_repair import occurrences, selector
from judicial_kg.extraction.repair_replay import replay_prepared
from judicial_kg.extraction.run import load_config, run_extraction
from judicial_kg.extraction.selector_stage import prepare_stages
from judicial_kg.sources.development import write_json
from judicial_kg.sources.records import canonical_hash, sha256
from judicial_kg.validation_run import strict_json

VERSION = "qwen-local-review-v1"
CONSTRAINED_VERSION = "qwen-local-review-v2"


def references(value, wanted):
    if isinstance(value, dict):
        return any(references(v, wanted) for v in value.values())
    if isinstance(value, list):
        return any(references(v, wanted) for v in value)
    return value == wanted


def build_plan(doc, draft, contract):
    positions = selector_repair.build_plan(doc, draft, contract)
    prior = attribution_repair.build_plan(doc, draft, contract)
    units = {u["id"]: u for u in source_units(doc)}
    mentions, properties = [], []
    for item in positions["selector_items"]:
        if item["target_kind"] == "mention":
            original = draft["mentions"][item["mention_index"]]
            unit = units.get(original["evidence"]["unit_id"])
            if unit is not None:
                mentions.append(
                    {
                        "mention_id": original["id"],
                        "index": item["mention_index"],
                        "original": original,
                        "unit": unit,
                    }
                )
        elif not item["choices"]:
            path = item["path"][:-2]
            properties.append(
                {
                    "statement_id": item["statement"]["id"],
                    "path": path,
                    "original": item["statement"],
                    "context_id": item["context_id"],
                    "scope_units": item["scope_units"],
                }
            )
    actors = [
        {"actor_id": a["id"], "index": i, "original": a}
        for i, a in enumerate(draft["actors"])
        if not references(draft["mentions"], a["id"]) and not references(draft["contexts"], a["id"])
    ]
    return {
        "document_id": doc["document_id"],
        "draft_sha256": canonical_hash(draft),
        "source_units": list(units.values()),
        "actors": draft["actors"],
        "grounded_mentions": positions["grounded_mentions"],
        "context_items": prior["context_items"],
        "mention_items": mentions,
        "property_items": properties,
        "actor_items": actors,
        "reference_status": "model_review_not_independent_gold",
    }


def response_schema():
    defs = {
        "context": {
            "context_id": {"type": "string"},
            "action": {"enum": ["classify", "uncertain"]},
            "kind": {"enum": [None, "party_claim", "party_defense", "quoted_material"]},
        },
        "mention": {
            "mention_id": {"type": "string"},
            "action": {"enum": ["local_span", "reject_unsupported", "uncertain"]},
            "local_text": {"type": ["string", "null"]},
            "kind": {"enum": [None, "name", "pronoun", "description"]},
        },
        "property": {
            "statement_id": {"type": "string"},
            "action": {"enum": ["reject_outside_context", "uncertain"]},
        },
        "actor": {
            "actor_id": {"type": "string"},
            "action": {"enum": ["reject_unreferenced", "uncertain"]},
        },
    }
    props = {}
    for kind, fields in defs.items():
        fields["reason"] = {"type": "string", "minLength": 1, "maxLength": 300}
        props[kind + "_choices"] = {
            "type": "array",
            "items": {
                "type": "object",
                "properties": fields,
                "required": list(fields),
                "additionalProperties": False,
            },
        }
    return {
        "type": "object",
        "properties": props,
        "required": list(props),
        "additionalProperties": False,
    }


def stage_choices(draft, plan, choices):
    if canonical_hash(draft) != plan["draft_sha256"]:
        raise ValueError("Local review differs from frozen draft")
    if list(Draft202012Validator(response_schema()).iter_errors(choices)):
        raise DraftError("local_review_schema_invalid")
    patched = copy.deepcopy(draft)
    rejected, pending = [], []
    for kind in ["context", "mention", "property", "actor"]:
        key = "statement_id" if kind == "property" else kind + "_id"
        ds = choices[kind + "_choices"]
        ids = [d[key] for d in ds]
        items = plan[kind + "_items"]
        if len(ids) != len(set(ids)) or set(ids) != {i[key] for i in items}:
            raise DraftError("local_review_coverage_mismatch")
        decisions = {d[key]: d for d in ds}
        for item in items:
            d = decisions[item[key]]
            if d["action"] == "uncertain":
                if any(d.get(k) is not None for k in ["local_text", "kind"]):
                    raise DraftError("local_review_uncertain_has_patch")
                pending.append(
                    {"code": "local_review_uncertain", "path": "/" + kind + "/" + item[key]}
                )
                continue
            if kind == "context":
                if d["kind"] not in item["allowed_kinds"]:
                    raise DraftError("local_review_context_outside_catalog")
                patched["contexts"][item["index"]]["kind"] = d["kind"]
            elif kind == "mention" and d["action"] == "local_span":
                positions = occurrences([item["unit"]], d["local_text"] or "")
                if not d["local_text"] or len(positions) != 1 or d["kind"] is None:
                    raise DraftError("local_review_span_not_unique")
                m = patched["mentions"][item["index"]]
                m["evidence"], m["kind"] = selector(positions[0]), d["kind"]
            else:
                if kind == "mention":
                    if d["local_text"] is not None or d["kind"] is not None:
                        raise DraftError("local_review_rejection_has_patch")
                    if item["original"]["evidence"]["text"] in item["unit"]["text"]:
                        raise DraftError("local_review_cannot_reject_literal_in_unit")
                rejected.append(
                    {
                        "kind": kind,
                        "original_assertion": item["original"],
                        "decision": d,
                        "source_catalog": item,
                        "status": "explicit_model_rejection_not_gold",
                    }
                )
    # Preserve ordering/paths while validating updates; removals happen last.
    restored = copy.deepcopy(patched)
    restored["mentions"] = copy.deepcopy(draft["mentions"])
    for item in plan["context_items"]:
        restored["contexts"][item["index"]]["kind"] = item["original"]["kind"]
    if restored != draft:
        raise ValueError("Local review changed unrelated facts")
    for kind, field in [("mention", "mentions"), ("actor", "actors")]:
        ids = {r["original_assertion"]["id"] for r in rejected if r["kind"] == kind}
        patched[field] = [v for v in patched[field] if v["id"] not in ids]
    for r in rejected:
        if r["kind"] != "property":
            continue
        path = r["source_catalog"]["path"]
        collection = selector_repair.at(patched, path[:-1])
        collection[:] = [v for v in collection if v["id"] != r["original_assertion"]["id"]]
    # Unreferenced actors must remain unreferenced after all source-bound changes.
    for r in rejected:
        if r["kind"] == "actor" and (
            references(patched["mentions"], r["original_assertion"]["id"])
            or references(patched["contexts"], r["original_assertion"]["id"])
        ):
            raise DraftError("local_review_actor_is_referenced")
    return patched, rejected, pending


def apply_choices(draft, plan, choices):
    patched, rejected, pending = stage_choices(draft, plan, choices)
    if pending:
        raise GroundingError(pending)
    return patched, rejected


class LocalReview(selector_repair.SelectorRepair):
    PROMPT_FILE = "prompts/qwen-local-review-v1.txt"
    RUN_KIND = "local_qwen_staged_assertion_review_not_gold"

    def draft_schema(self, contract):
        return response_schema()

    def check_selection(self, docs, config):
        if config["version"] != VERSION or [d["document_id"] for d in docs] != self.selection_ids:
            raise ValueError("Local review must retain frozen targets")

    def materialize(self, content, doc, contract, provenance):
        try:
            choices = strict_json(content)
        except (TypeError, ValueError):
            raise DraftError("local_review_invalid_json") from None
        draft, rejected = apply_choices(
            self.drafts[doc["document_id"]], self.plans[doc["document_id"]], choices
        )
        stem = sha256(doc["document_id"].encode())[:16]
        write_json(self.output / ("rejected-assertions-" + stem + ".json"), rejected)
        write_json(self.output / ("patched-" + stem + ".json"), draft)
        self.decision_counts[doc["document_id"]] = {
            kind: len(choices[kind + "_choices"])
            for kind in ["context", "mention", "property", "actor"]
        }
        return contextual.materialize(
            json.dumps(draft, ensure_ascii=False), doc, contract, provenance
        )


class ConstrainedLocalReview(LocalReview):
    PROMPT_FILE = "prompts/qwen-local-review-v2.txt"
    GENERATION_CONSTRAINT_VERSION = "bounded-local-review-conclusion-before-action-v1"
    GENERATION_CONSTRAINT_SCOPE = "review_ids_counts_short_source_conclusion_before_action"

    def draft_schema(self, contract):
        schema = response_schema()
        for array in schema["properties"].values():
            item = array["items"]
            props = item["properties"]
            props["reason"]["maxLength"] = 80
            identifier = next(iter(props))
            item["properties"] = {
                identifier: props[identifier],
                "reason": props["reason"],
                **{k: v for k, v in props.items() if k not in {identifier, "reason"}},
            }
        return schema

    def bound_schema(self, doc):
        schema = self.draft_schema(None)
        plan = self.plans[doc["document_id"]]
        for kind, identifier in {
            "context": "context_id",
            "mention": "mention_id",
            "property": "statement_id",
            "actor": "actor_id",
        }.items():
            items = plan[kind + "_items"]
            array = schema["properties"][kind + "_choices"]
            array.update(minItems=len(items), maxItems=len(items))
            if items:
                array["items"]["properties"][identifier]["enum"] = [i[identifier] for i in items]
        return schema

    def response_format(self, doc, schema):
        return constrained.response_format(doc, self.bound_schema(doc))

    def messages(self, doc, prompt, schema):
        return super().messages(doc, prompt, self.bound_schema(doc))

    def check_selection(self, docs, config):
        if (
            config["version"] != CONSTRAINED_VERSION
            or [d["document_id"] for d in docs] != self.selection_ids
        ):
            raise ValueError("Local review must retain frozen targets")

    def materialize(self, content, doc, contract, provenance):
        try:
            choices = strict_json(content)
        except (TypeError, ValueError):
            raise DraftError("local_review_invalid_json") from None
        if list(Draft202012Validator(self.bound_schema(doc)).iter_errors(choices)):
            raise DraftError("local_review_schema_invalid")
        return super().materialize(content, doc, contract, provenance)


def prepare_repair(root, documents, specification, saved, version=VERSION):
    if version not in {VERSION, CONSTRAINED_VERSION}:
        raise ValueError("Unsupported local review version")
    folder = root / "artifacts/local-review-source-verification" / uuid4().hex
    proof = prepare_stages(root, documents, specification, saved, folder)
    stages = strict_json((folder / "stages.json").read_bytes())
    docs = [d for p in documents for d in strict_json(p.read_bytes())]
    contract = Contract.load(root, "0.2.0")
    drafts, plans, ids = {}, {}, []
    for doc in docs:
        key = doc["document_id"]
        if key not in stages or stages[key]["status"] != "unresolved":
            continue
        draft = stages[key]["draft"]
        plan = build_plan(doc, draft, contract)
        if not any(plan[k + "_items"] for k in ["context", "mention", "property", "actor"]):
            continue
        drafts[key], plans[key] = draft, plan
        ids.append(key)
    if not ids:
        raise ValueError("No supported local review targets")
    factory = ConstrainedLocalReview if version == CONSTRAINED_VERSION else LocalReview
    return factory(
        drafts,
        plans,
        ids,
        {
            "stage_report": proof,
            "stage_drafts_sha256": canonical_hash({k: stages[k] for k in ids}),
            "parent_zero_call_replay_verified": True,
            "rejections_retained_not_gold": True,
        },
    )


def run_repair(root, documents, specification, saved, config, output, client=None):
    return run_extraction(
        root,
        documents,
        config,
        output,
        client,
        prepare_repair(root, documents, specification, saved, load_config(config)["version"]),
    )


def replay_repair(root, documents, specification, saved, run, output):
    version = strict_json((run / "report.json").read_bytes())["config"]["version"]
    return replay_prepared(
        root,
        documents,
        run,
        output,
        prepare_repair(root, documents, specification, saved, version),
        version,
    )
