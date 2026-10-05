"""Bounded context reclassification and explicit unsupported-assertion review."""

import copy
import json
from uuid import uuid4

from jsonschema import Draft202012Validator

from judicial_kg.contracts import Contract
from judicial_kg.extraction import contextual, selector_repair
from judicial_kg.extraction.anchored import GroundingError
from judicial_kg.extraction.draft import DraftError
from judicial_kg.extraction.repair_replay import replay_prepared
from judicial_kg.extraction.run import completion_content, run_extraction
from judicial_kg.sources.development import write_json
from judicial_kg.sources.records import canonical_hash, sha256
from judicial_kg.validation_run import strict_json

VERSION = "qwen-attribution-repair-v1"


def build_plan(doc, draft, contract):
    selectors = selector_repair.build_plan(doc, draft, contract)
    actors = {a["id"]: a["actor_type"] for a in draft["actors"]}
    contexts = []
    for index, context in enumerate(draft["contexts"]):
        ids = context["attributed_actor_ids"]
        if (
            context["kind"].startswith("court_")
            and ids
            and any(actors.get(k) != "court" for k in ids)
        ):
            if any(k not in actors or actors[k] == "court" for k in ids):
                continue
            contexts.append(
                {
                    "context_id": context["id"],
                    "index": index,
                    "original": context,
                    "allowed_kinds": ["party_claim", "party_defense", "quoted_material"],
                }
            )
    mentions = []
    for item in selectors["selector_items"]:
        m = draft["mentions"][item["mention_index"]]
        unit = item["original_unit"]
        peers = [
            g
            for g in selectors["grounded_mentions"]
            if g["unit_id"] == item["original_selector"]["unit_id"]
            and g["actor_id"] != item["actor_id"]
        ]
        # Only name assertions whose literal is absent from the original unit,
        # with no unoccupied position and a grounded different actor in that unit.
        if m["kind"] == "name" and not item["choices"] and unit and peers:
            mentions.append(
                {"mention_id": m["id"], "original": m, "original_unit": unit, "source_peers": peers}
            )
    return {
        "document_id": doc["document_id"],
        "draft_sha256": canonical_hash(draft),
        "source_units": selectors["source_units"],
        "actors": draft["actors"],
        "context_items": contexts,
        "mention_items": mentions,
        "reference_status": "model_review_not_independent_gold",
    }


def response_schema():
    context = {
        "type": "object",
        "additionalProperties": False,
        "properties": {
            "context_id": {"type": "string"},
            "kind": {"enum": [None, "party_claim", "party_defense", "quoted_material"]},
            "action": {"enum": ["classify", "uncertain"]},
            "reason": {"type": "string", "minLength": 1, "maxLength": 300},
        },
    }
    mention = {
        "type": "object",
        "additionalProperties": False,
        "properties": {
            "mention_id": {"type": "string"},
            "source_peer_mention_id": {"type": ["string", "null"]},
            "action": {"enum": ["reject_unsupported", "uncertain"]},
            "reason": {"type": "string", "minLength": 1, "maxLength": 300},
        },
    }
    for item in [context, mention]:
        item["required"] = list(item["properties"])
    return {
        "type": "object",
        "additionalProperties": False,
        "properties": {
            "context_choices": {"type": "array", "items": context},
            "mention_choices": {"type": "array", "items": mention},
        },
        "required": ["context_choices", "mention_choices"],
    }


def apply_choices(draft, plan, decisions):
    if canonical_hash(draft) != plan["draft_sha256"]:
        raise ValueError("Attribution draft differs from frozen source")
    if list(Draft202012Validator(response_schema()).iter_errors(decisions)):
        raise DraftError("attribution_repair_schema_invalid")
    patched, rejected, issues = copy.deepcopy(draft), [], []
    for kind in ["context", "mention"]:
        id_key = kind + "_id"
        choices = decisions[kind + "_choices"]
        ids = [c[id_key] for c in choices]
        items = plan[kind + "_items"]
        if len(ids) != len(set(ids)) or set(ids) != {i[id_key] for i in items}:
            raise DraftError("attribution_repair_item_coverage_mismatch")
        by_id = {c[id_key]: c for c in choices}
        for item in items:
            choice = by_id[item[id_key]]
            if choice["action"] == "uncertain":
                patch_field = "kind" if kind == "context" else "source_peer_mention_id"
                if choice[patch_field] is not None:
                    raise DraftError("attribution_uncertain_has_patch")
                issues.append(
                    {
                        "code": "attribution_repair_uncertain",
                        "path": "/" + kind + "s/" + item[id_key],
                    }
                )
            elif kind == "context":
                if choice["kind"] not in item["allowed_kinds"]:
                    raise DraftError("attribution_kind_outside_catalog")
                patched["contexts"][item["index"]]["kind"] = choice["kind"]
            else:
                if choice["source_peer_mention_id"] not in {
                    p["mention_id"] for p in item["source_peers"]
                }:
                    raise DraftError("unsupported_mention_peer_outside_catalog")
                rejected.append(
                    {
                        "original_assertion": item["original"],
                        "source_unit": item["original_unit"],
                        "decision": choice,
                        "status": "explicit_model_rejection_not_gold",
                    }
                )
    if issues:
        raise GroundingError(issues)
    removed = {r["original_assertion"]["id"] for r in rejected}
    patched["mentions"] = [m for m in patched["mentions"] if m["id"] not in removed]
    restored = copy.deepcopy(patched)
    restored["mentions"] = copy.deepcopy(draft["mentions"])
    for item in plan["context_items"]:
        restored["contexts"][item["index"]]["kind"] = item["original"]["kind"]
    if restored != draft:
        raise ValueError("Attribution repair changed facts outside its limited catalog")
    return patched, rejected


class AttributionRepair(selector_repair.SelectorRepair):
    PROMPT_FILE = "prompts/qwen-attribution-repair-v1.txt"
    RUN_KIND = "local_qwen_bounded_attribution_review_not_gold"

    def draft_schema(self, contract):
        return response_schema()

    def check_selection(self, docs, config):
        if config["version"] != VERSION or [d["document_id"] for d in docs] != self.selection_ids:
            raise ValueError("Attribution selection must exactly retain frozen targets")

    def materialize(self, content, doc, contract, provenance):
        try:
            decisions = strict_json(content)
        except (ValueError, TypeError):
            raise DraftError("attribution_repair_invalid_json") from None
        patched, rejected = apply_choices(
            self.drafts[doc["document_id"]], self.plans[doc["document_id"]], decisions
        )
        stem = sha256(doc["document_id"].encode())[:16]
        write_json(self.output / ("rejected-assertions-" + stem + ".json"), rejected)
        write_json(self.output / ("patched-" + stem + ".json"), patched)
        self.decision_counts[doc["document_id"]] = {
            "contexts_reclassified": sum(
                c["action"] == "classify" for c in decisions["context_choices"]
            ),
            "unsupported_assertions_rejected": len(rejected),
        }
        return contextual.materialize(
            json.dumps(patched, ensure_ascii=False), doc, contract, provenance
        )


def prepare_repair(root, document_paths, specification, selector_run):
    parent = selector_repair.prepare_repair(root, document_paths, specification)
    # Validate parent decisions, source binding, hashes and all outcomes by the
    # existing zero-call replay before reusing any derived draft.
    verification = root / "artifacts/attribution-source-verification" / uuid4().hex
    proof = replay_prepared(
        root, document_paths, selector_run, verification, parent, selector_repair.VERSION
    )
    if not proof["accepted_candidates_match_saved"] or not proof["outcomes_match_saved"]:
        raise ValueError("Parent selector replay mismatch")
    original = strict_json((selector_run / "report.json").read_bytes())
    outcomes = {o["document_id"]: o for o in original["selected_outcomes"]}
    selected = strict_json((selector_run / "selected-documents.json").read_bytes())
    calls = [c for c in original["response_metadata"] if c["path"] == "/v1/chat/completions"]
    drafts, plans, ids = {}, {}, []
    for doc, call in zip(selected, calls, strict=True):
        key = doc["document_id"]
        if outcomes[key]["status"] not in {"draft_grounding_failed", "contract_rejected"}:
            continue
        payload = strict_json((selector_run / f"rpc-{call['rpc']:03}.response.bin").read_bytes())
        content, _ = completion_content(payload, original["config"]["model"])
        decisions = strict_json(content)
        draft = copy.deepcopy(parent.drafts[key])
        if all(c["action"] != "uncertain" for c in decisions["selector_choices"]):
            draft, _ = selector_repair.apply_choices(draft, parent.plans[key], decisions)
        plan = build_plan(doc, draft, Contract.load(root, "0.2.0"))
        if not plan["context_items"] and not plan["mention_items"]:
            continue
        drafts[key], plans[key] = draft, plan
        ids.append(key)
    if not ids:
        raise ValueError("No supported attribution-review targets")
    return AttributionRepair(
        drafts,
        plans,
        ids,
        {
            "source_selector_report_sha256": sha256((selector_run / "report.json").read_bytes()),
            "parent_lineage": parent.lineage,
            "parent_zero_call_replay_verified": True,
            "explicit_rejected_assertions_retained": True,
            "plan_counts": {
                k: {"contexts": len(p["context_items"]), "mentions": len(p["mention_items"])}
                for k, p in plans.items()
            },
        },
    )


def run_repair(root, documents, specification, selector_run, config, output, client=None):
    protocol = prepare_repair(root, documents, specification, selector_run)
    return run_extraction(root, documents, config, output, client, protocol)


def replay_repair(root, documents, specification, selector_run, saved, output):
    protocol = prepare_repair(root, documents, specification, selector_run)
    return replay_prepared(root, documents, saved, output, protocol, VERSION)
