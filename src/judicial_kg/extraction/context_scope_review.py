"""Frozen context rehoming; no scope widening, assertion deletion or speaker changes."""

import copy
import json
from uuid import uuid4

from jsonschema import Draft202012Validator

from judicial_kg.contracts import Contract
from judicial_kg.extraction import actor_grounded, constrained, contextual, selector_repair
from judicial_kg.extraction.anchored import GroundingError
from judicial_kg.extraction.draft import DraftError
from judicial_kg.extraction.repair_replay import replay_prepared
from judicial_kg.extraction.replay import load_saved_run, replay_completed
from judicial_kg.extraction.run import completion_content, run_extraction
from judicial_kg.sources.development import write_json
from judicial_kg.sources.records import canonical_hash, sha256
from judicial_kg.validation_run import strict_json, validate_batch

VERSION = "qwen-context-scope-review-v1"


def shell(context):
    return {
        k: copy.deepcopy(v)
        for k, v in context.items()
        if k not in {"roles", "events", "statements"}
    }


def build_plan(doc, draft, contract):
    if list(Draft202012Validator(contextual.draft_schema(contract)).iter_errors(draft)):
        raise DraftError("context_scope_source_schema_invalid")
    positions = selector_repair.build_plan(doc, draft, contract)
    units = {u["id"]: u for u in positions["source_units"]}
    contexts = draft["contexts"]
    ids = [c["id"] for c in contexts]
    if len(set(ids)) != len(ids):
        raise ValueError("Context scope catalog requires unique context IDs")
    actors = {a["id"]: a for a in draft["actors"]}
    items = []
    for ci, context in enumerate(contexts):
        for collection in ("roles", "events"):
            for ni, node in enumerate(context[collection]):
                required = list(node["evidence_units"])
                # Event properties travel with their Event and retain their original quotes.
                if collection == "events":
                    required.extend(s["value"]["source"]["unit_id"] for s in node["statements"])
                required = list(dict.fromkeys(required))
                outside = [u for u in required if u not in context["evidence_units"]]
                if not outside:
                    continue
                compatible = [
                    c
                    for c in contexts
                    if c["id"] != context["id"]
                    and all(
                        c[k] == context[k]
                        for k in ("kind", "attributed_actor_ids", "parent_context_id")
                    )
                    and set(required) <= set(c["evidence_units"])
                ]
                choices = [
                    {"choice_id": "existing:" + c["id"], "new_context": False, "context": shell(c)}
                    for c in compatible
                ]
                grounded = [
                    m
                    for m in positions["grounded_mentions"]
                    if collection == "roles"
                    and m["actor_id"] == node["actor_id"]
                    and m["unit_id"] in required
                    and any(
                        x["id"] == m["mention_id"] and x["kind"] == "name"
                        for x in draft["mentions"]
                    )
                ]
                # A separately reviewed footer sibling is narrowly permitted only for
                # grounded person roles from a root procedural record. It does not
                # turn a claim into a finding or widen the original header context.
                if (
                    collection == "roles"
                    and context["kind"] == "procedural_record"
                    and context["parent_context_id"] is None
                    and actors.get(node["actor_id"], {}).get("actor_type") == "person"
                    and grounded
                    and all(u in units and units[u]["field"] == "footer" for u in required)
                ):
                    new_id = "scope_" + canonical_hash([context["id"], required])[:16]
                    if new_id in ids:
                        raise ValueError("Generated context ID collides with source")
                    target = shell(context)
                    target.update(id=new_id, evidence_units=required)
                    choices.append(
                        {"choice_id": "footer:" + new_id, "new_context": True, "context": target}
                    )
                items.append(
                    {
                        "item_id": f"{context['id']}:{collection}:{node['id']}",
                        "context_index": ci,
                        "node_index": ni,
                        "collection": collection,
                        "original_context": shell(context),
                        "original": copy.deepcopy(node),
                        "required_units": required,
                        "outside_units": outside,
                        "grounded_mentions": grounded,
                        "choices": choices,
                    }
                )
    return {
        "document_id": doc["document_id"],
        "draft_sha256": canonical_hash(draft),
        "source_units": positions["source_units"],
        "contexts": [shell(c) for c in contexts],
        "scope_items": items,
        "baseline_used": False,
        "reference_status": "model_context_scope_review_not_gold",
    }


def response_schema(plan=None):
    props = {
        "item_id": {"type": "string"},
        "reason": {"type": "string", "minLength": 1, "maxLength": 80},
        "action": {"enum": ["rehome", "uncertain"]},
        "choice_id": {"type": ["string", "null"]},
    }
    array = {
        "type": "array",
        "items": {
            "type": "object",
            "properties": props,
            "required": list(props),
            "additionalProperties": False,
        },
    }
    if plan is not None:
        items = plan["scope_items"]
        array.update(minItems=len(items), maxItems=len(items))
        props["item_id"]["enum"] = [i["item_id"] for i in items]
        props["choice_id"]["enum"] = sorted(
            {c["choice_id"] for i in items for c in i["choices"]}
        ) + [None]
    return {
        "type": "object",
        "properties": {"scope_choices": array},
        "required": ["scope_choices"],
        "additionalProperties": False,
    }


def apply_choices(doc, draft, plan, choices, contract):
    if canonical_hash(draft) != plan["draft_sha256"]:
        raise ValueError("Context scope draft differs from frozen source")
    if build_plan(doc, draft, contract) != plan:
        raise ValueError("Context scope plan differs from immutable source catalog")
    if list(Draft202012Validator(response_schema(plan)).iter_errors(choices)):
        raise DraftError("context_scope_review_schema_invalid")
    decisions = choices["scope_choices"]
    ids = [d["item_id"] for d in decisions]
    if len(set(ids)) != len(ids) or set(ids) != {i["item_id"] for i in plan["scope_items"]}:
        raise DraftError("context_scope_review_coverage_mismatch")
    by_id = {d["item_id"]: d for d in decisions}
    moves, pending = [], []
    for item in plan["scope_items"]:
        decision = by_id[item["item_id"]]
        if decision["action"] == "uncertain":
            if decision["choice_id"] is not None:
                raise DraftError("context_scope_uncertain_has_patch")
            pending.append(
                {"code": "context_scope_review_uncertain", "path": "/scope/" + item["item_id"]}
            )
            continue
        match = [c for c in item["choices"] if c["choice_id"] == decision["choice_id"]]
        if len(match) != 1:
            raise DraftError("context_scope_choice_outside_item_catalog")
        moves.append((item, match[0], decision))
    if pending:
        raise GroundingError(pending)
    patched = copy.deepcopy(draft)
    by_context = {c["id"]: c for c in patched["contexts"]}
    # Remove by frozen indexes in descending order before appending destinations.
    for item, _, _ in sorted(
        moves,
        key=lambda m: (m[0]["context_index"], m[0]["collection"], m[0]["node_index"]),
        reverse=True,
    ):
        removed = patched["contexts"][item["context_index"]][item["collection"]].pop(
            item["node_index"]
        )
        if removed != item["original"]:
            raise ValueError("Context scope source assertion changed")
    ledger = []
    for item, target, decision in moves:
        identifier = target["context"]["id"]
        if identifier not in by_context:
            created = {
                **copy.deepcopy(target["context"]),
                "roles": [],
                "events": [],
                "statements": [],
            }
            patched["contexts"].append(created)
            by_context[identifier] = created
        by_context[identifier][item["collection"]].append(copy.deepcopy(item["original"]))
        ledger.append(
            {
                "item_id": item["item_id"],
                "from_context": item["original_context"],
                "to_context": target["context"],
                "assertion_before": item["original"],
                "assertion_after": copy.deepcopy(item["original"]),
                "decision": decision,
            }
        )
    # Global conservation includes event participants/properties, polarity, IDs and quotes.
    for collection in ("roles", "events", "statements"):
        before = sorted(canonical_hash(n) for c in draft["contexts"] for n in c[collection])
        after = sorted(canonical_hash(n) for c in patched["contexts"] for n in c[collection])
        if before != after:
            raise ValueError("Context scope review changed or lost assertions")
    if any(
        shell(a) != shell(b) for a, b in zip(draft["contexts"], patched["contexts"], strict=False)
    ):
        raise ValueError("Context scope review widened or reclassified an original context")
    if any(patched[k] != draft[k] for k in draft if k != "contexts"):
        raise ValueError("Context scope review changed actors or mentions")
    return patched, ledger


class ContextScopeReview(selector_repair.SelectorRepair):
    PROMPT_FILE = "prompts/qwen-context-scope-review-v1.txt"
    RUN_KIND = "local_qwen_bounded_context_scope_review_not_gold"
    GENERATION_CONSTRAINT_VERSION = "frozen-context-scope-catalog-v1"
    GENERATION_CONSTRAINT_SCOPE = "scope_item_ids_count_choices_and_reason_length"

    def draft_schema(self, contract):
        return response_schema()

    def check_selection(self, docs, config):
        if config["version"] != VERSION or [d["document_id"] for d in docs] != self.selection_ids:
            raise ValueError("Context scope review requires frozen targets")

    def bound_schema(self, doc):
        return response_schema(self.plans[doc["document_id"]])

    def response_format(self, doc, schema):
        return constrained.response_format(doc, self.bound_schema(doc))

    def messages(self, doc, prompt, schema):
        return super().messages(doc, prompt, self.bound_schema(doc))

    def materialize(self, content, doc, contract, provenance):
        try:
            choices = strict_json(content)
        except (TypeError, ValueError):
            raise DraftError("context_scope_review_invalid_json") from None
        patched, ledger = apply_choices(
            doc, self.drafts[doc["document_id"]], self.plans[doc["document_id"]], choices, contract
        )
        stem = sha256(doc["document_id"].encode())[:16]
        write_json(self.output / ("patched-" + stem + ".json"), patched)
        write_json(self.output / ("scope-ledger-" + stem + ".json"), ledger)
        self.decision_counts[doc["document_id"]] = {
            "assertions_rehomed": len(ledger),
            "new_contexts": len(patched["contexts"])
            - len(self.drafts[doc["document_id"]]["contexts"]),
        }
        return contextual.materialize(
            json.dumps(patched, ensure_ascii=False), doc, contract, provenance
        )


def prepare_repair(root, documents, spec):
    docs = [d for p in documents for d in strict_json(p.read_bytes())]
    contract = Contract.load(root, "0.2.0")
    validate_batch(docs, [], contract)
    all_docs = {d["document_id"]: d for d in docs}
    drafts, plans, ids, lineage = {}, {}, [], []
    raw_spec = spec.read_bytes()
    for entry in strict_json(raw_spec):
        paths, run = [root / p for p in entry["documents"]], root / entry["source_run"]
        raw, report, protocol, _, source_docs, _, selected, calls, responses = load_saved_run(
            root, paths, run
        )
        if {d["document_id"]: d for d in source_docs} != all_docs:
            raise ValueError("Context review must preserve the full source denominator")
        proof = replay_completed(
            root, paths, run, root / "artifacts/context-scope-source-verification" / uuid4().hex
        )
        source_outcomes = {o["document_id"]: o for o in report["selected_outcomes"]}
        if any(
            any(o[k] != source_outcomes[o["document_id"]][k] for k in ("status", "issues"))
            for o in proof["selected_outcomes"]
        ):
            raise ValueError("Context scope source replay mismatch")
        # Report output hashes are verified by load_saved_run; compare regenerated records too.
        accepted_hash = proof["outputs_sha256"]["accepted.json"]
        if accepted_hash != report["outputs_sha256"]["accepted.json"]:
            raise ValueError("Context scope source accepted records mismatch")
        targets = entry["document_ids"]
        outcomes = {o["document_id"]: o for o in report["selected_outcomes"]}
        if (
            not targets
            or len(set(targets)) != len(targets)
            or any(
                k not in outcomes
                or outcomes[k]["status"] not in {"draft_grounding_failed", "contract_rejected"}
                for k in targets
            )
        ):
            raise ValueError("Context scope review requires rejected complete source drafts")
        for doc, response in zip(selected, responses, strict=True):
            key = doc["document_id"]
            if key not in targets:
                continue
            if key in ids:
                raise ValueError("Duplicate context review target")
            content, _ = completion_content(response, report["config"]["model"])
            draft = (
                actor_grounded.unpack_draft(content, contract)
                if getattr(protocol, "unpack_draft", None) is actor_grounded.unpack_draft
                else contextual.unpack_draft(content)
            )
            plan = build_plan(doc, draft, contract)
            if not plan["scope_items"]:
                raise ValueError("No context scope error in selected draft")
            drafts[key], plans[key] = draft, plan
            ids.append(key)
        lineage.append(
            {
                "source_run_id": report["run_id"],
                "source_report_sha256": sha256(raw),
                "source_protocol": report["config"]["version"],
                "target_outcomes": [outcomes[k] for k in targets],
                "response_sha256": [
                    c["response_sha256"]
                    for d, c in zip(selected, calls, strict=True)
                    if d["document_id"] in targets
                ],
                "source_zero_call_replay_verified": True,
            }
        )
    if not ids:
        raise ValueError("No context scope targets")
    return ContextScopeReview(
        drafts,
        plans,
        ids,
        {
            "specification_sha256": sha256(raw_spec),
            "sources": lineage,
            "baseline_used": False,
            "no_assertions_deleted": True,
            "no_original_context_widened_or_reclassified": True,
        },
    )


def run_repair(root, docs, spec, config, output, client=None):
    return run_extraction(root, docs, config, output, client, prepare_repair(root, docs, spec))


def replay_repair(root, docs, spec, saved, output):
    return replay_prepared(root, docs, saved, output, prepare_repair(root, docs, spec), VERSION)
