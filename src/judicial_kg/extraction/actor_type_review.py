"""Grounded court-type correction; no actor identity or source span changes."""

import copy
import json
from uuid import uuid4

from jsonschema import Draft202012Validator

from judicial_kg.contracts import Contract
from judicial_kg.extraction import contextual, local_review, selector_repair
from judicial_kg.extraction.anchored import GroundingError
from judicial_kg.extraction.draft import DraftError
from judicial_kg.extraction.repair_replay import replay_prepared
from judicial_kg.extraction.run import completion_content, run_extraction
from judicial_kg.sources.development import write_json
from judicial_kg.sources.records import canonical_hash, sha256
from judicial_kg.validation_run import strict_json

VERSION = "qwen-actor-type-review-v1"


def build_plan(doc, draft, contract):
    positions = selector_repair.build_plan(doc, draft, contract)
    wanted = {
        a
        for c in draft["contexts"]
        if c["kind"].startswith("court_")
        for a in c["attributed_actor_ids"]
    }
    items = []
    for index, actor in enumerate(draft["actors"]):
        grounded = [m for m in positions["grounded_mentions"] if m["actor_id"] == actor["id"]]
        court_names = [m for m in grounded if m["text"].endswith("法院")]
        if actor["id"] in wanted and actor["actor_type"] != "court" and court_names:
            items.append(
                {
                    "actor_id": actor["id"],
                    "index": index,
                    "original": actor,
                    "grounded_mentions": grounded,
                    "court_name_evidence": court_names,
                }
            )
    return {
        "document_id": doc["document_id"],
        "draft_sha256": canonical_hash(draft),
        "actor_type_items": items,
        "source_units": positions["source_units"],
        "reference_status": "model_type_review_not_gold",
    }


def response_schema():
    props = {
        "actor_id": {"type": "string"},
        "action": {"enum": ["classify", "uncertain"]},
        "actor_type": {"enum": [None, "court"]},
        "reason": {"type": "string", "minLength": 1, "maxLength": 300},
    }
    return {
        "type": "object",
        "properties": {
            "actor_type_choices": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": props,
                    "required": list(props),
                    "additionalProperties": False,
                },
            }
        },
        "required": ["actor_type_choices"],
        "additionalProperties": False,
    }


def apply_choices(draft, plan, choices):
    if canonical_hash(draft) != plan["draft_sha256"]:
        raise ValueError("Actor type review draft differs from frozen source")
    if list(Draft202012Validator(response_schema()).iter_errors(choices)):
        raise DraftError("actor_type_review_schema_invalid")
    ds = choices["actor_type_choices"]
    ids = [d["actor_id"] for d in ds]
    if len(ids) != len(set(ids)) or set(ids) != {i["actor_id"] for i in plan["actor_type_items"]}:
        raise DraftError("actor_type_review_coverage_mismatch")
    byid = {d["actor_id"]: d for d in ds}
    patched = copy.deepcopy(draft)
    pending = []
    for item in plan["actor_type_items"]:
        d = byid[item["actor_id"]]
        if d["action"] == "uncertain":
            if d["actor_type"] is not None:
                raise DraftError("actor_type_uncertain_has_patch")
            pending.append(
                {"code": "actor_type_review_uncertain", "path": "/actors/" + item["actor_id"]}
            )
        elif d["actor_type"] == "court":
            patched["actors"][item["index"]]["actor_type"] = "court"
        else:
            raise DraftError("actor_type_outside_catalog")
    if pending:
        raise GroundingError(pending)
    restored = copy.deepcopy(patched)
    for item in plan["actor_type_items"]:
        restored["actors"][item["index"]]["actor_type"] = item["original"]["actor_type"]
    if restored != draft:
        raise ValueError("Actor type review changed identity or unrelated facts")
    return patched


class ActorTypeReview(selector_repair.SelectorRepair):
    PROMPT_FILE = "prompts/qwen-actor-type-review-v1.txt"
    RUN_KIND = "local_qwen_grounded_actor_type_review_not_gold"

    def draft_schema(self, contract):
        return response_schema()

    def check_selection(self, docs, config):
        if config["version"] != VERSION or [d["document_id"] for d in docs] != self.selection_ids:
            raise ValueError("Actor type review requires frozen targets")

    def save_plan(self, output):
        super().save_plan(output)
        write_json(output / "parent-rejected-assertions.json", self.parent_rejections)

    def materialize(self, content, doc, contract, provenance):
        try:
            choices = strict_json(content)
        except (TypeError, ValueError):
            raise DraftError("actor_type_review_invalid_json") from None
        patched = apply_choices(
            self.drafts[doc["document_id"]], self.plans[doc["document_id"]], choices
        )
        stem = sha256(doc["document_id"].encode())[:16]
        write_json(self.output / ("patched-" + stem + ".json"), patched)
        self.decision_counts[doc["document_id"]] = {
            "actor_types_corrected": len(choices["actor_type_choices"])
        }
        return contextual.materialize(
            json.dumps(patched, ensure_ascii=False), doc, contract, provenance
        )


def prepare_repair(root, documents, spec, selector_run, local_run):
    parent = local_review.prepare_repair(root, documents, spec, selector_run)
    folder = root / "artifacts/actor-type-source-verification" / uuid4().hex
    proof = replay_prepared(root, documents, local_run, folder, parent, local_review.VERSION)
    if not proof["accepted_candidates_match_saved"] or not proof["outcomes_match_saved"]:
        raise ValueError("Parent local review replay mismatch")
    report = strict_json((local_run / "report.json").read_bytes())
    selected = strict_json((local_run / "selected-documents.json").read_bytes())
    calls = [c for c in report["response_metadata"] if c["path"] == "/v1/chat/completions"]
    outcomes = {o["document_id"]: o for o in report["selected_outcomes"]}
    drafts, plans, ids, rejections = {}, {}, [], {}
    for doc, call in zip(selected, calls, strict=True):
        k = doc["document_id"]
        if outcomes[k]["status"] not in {"draft_grounding_failed", "contract_rejected"}:
            continue
        raw = strict_json((local_run / f"rpc-{call['rpc']:03}.response.bin").read_bytes())
        content, _ = completion_content(raw, report["config"]["model"])
        staged, rejected, pending = local_review.stage_choices(
            parent.drafts[k], parent.plans[k], strict_json(content)
        )
        # This protocol only resolves context checks through court actor type.
        # Property/mention uncertainty must stay rejected, not vanish from lineage.
        if any(not p["path"].startswith("/context/") for p in pending):
            continue
        plan = build_plan(doc, staged, Contract.load(root, "0.2.0"))
        if not plan["actor_type_items"]:
            continue
        drafts[k], plans[k], rejections[k] = staged, plan, rejected
        ids.append(k)
    if not ids:
        raise ValueError("No grounded actor type targets")
    result = ActorTypeReview(
        drafts,
        plans,
        ids,
        {
            "parent_local_report_sha256": sha256((local_run / "report.json").read_bytes()),
            "parent_local_lineage": parent.lineage,
            "parent_rejected_assertions_sha256": canonical_hash(rejections),
            "parent_zero_call_replay_verified": True,
        },
    )
    result.parent_rejections = rejections
    return result


def run_repair(root, docs, spec, selector_run, local_run, config, output, client=None):
    return run_extraction(
        root,
        docs,
        config,
        output,
        client,
        prepare_repair(root, docs, spec, selector_run, local_run),
    )


def replay_repair(root, docs, spec, selector_run, local_run, saved, output):
    return replay_prepared(
        root,
        docs,
        saved,
        output,
        prepare_repair(root, docs, spec, selector_run, local_run),
        VERSION,
    )
