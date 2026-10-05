"""Review a property unsupported by its speaker context, preserving other claims."""

from uuid import uuid4

from judicial_kg.contracts import Contract
from judicial_kg.extraction import local_review
from judicial_kg.extraction.repair_replay import replay_prepared
from judicial_kg.extraction.run import completion_content, run_extraction
from judicial_kg.sources.development import write_json
from judicial_kg.sources.records import canonical_hash, sha256
from judicial_kg.validation_run import strict_json

VERSION = "qwen-property-scope-review-v1"


class PropertyScopeReview(local_review.LocalReview):
    PROMPT_FILE = "prompts/qwen-property-scope-review-v1.txt"
    RUN_KIND = "local_qwen_speaker_bound_property_review_not_gold"

    def check_selection(self, docs, config):
        if config["version"] != VERSION or [d["document_id"] for d in docs] != self.selection_ids:
            raise ValueError("Property scope review requires frozen targets")

    def save_plan(self, output):
        super().save_plan(output)
        write_json(output / "parent-rejected-assertions.json", self.parent_rejections)


def prepare_repair(root, documents, spec, selector_run, local_run):
    parent = local_review.prepare_repair(root, documents, spec, selector_run)
    folder = root / "artifacts/property-scope-source-verification" / uuid4().hex
    proof = replay_prepared(root, documents, local_run, folder, parent, local_review.VERSION)
    if not proof["accepted_candidates_match_saved"] or not proof["outcomes_match_saved"]:
        raise ValueError("Parent local replay mismatch")
    report = strict_json((local_run / "report.json").read_bytes())
    docs = strict_json((local_run / "selected-documents.json").read_bytes())
    calls = [c for c in report["response_metadata"] if c["path"] == "/v1/chat/completions"]
    outcomes = {o["document_id"]: o for o in report["selected_outcomes"]}
    drafts, plans, ids, rejections = {}, {}, [], {}
    for doc, call in zip(docs, calls, strict=True):
        k = doc["document_id"]
        if outcomes[k]["status"] not in {"draft_grounding_failed", "contract_rejected"}:
            continue
        payload = strict_json((local_run / f"rpc-{call['rpc']:03}.response.bin").read_bytes())
        content, _ = completion_content(payload, report["config"]["model"])
        staged, rejected, pending = local_review.stage_choices(
            parent.drafts[k], parent.plans[k], strict_json(content)
        )
        if not pending or any(not p["path"].startswith("/property/") for p in pending):
            continue
        plan = local_review.build_plan(doc, staged, Contract.load(root, "0.2.0"))
        if not plan["property_items"] or any(
            plan[t + "_items"] for t in ["context", "mention", "actor"]
        ):
            continue
        # Explicitly expose event identity and speakers: dates in another context
        # cannot become evidence for this speaker just because literals match.
        plan["context_event_comparison"] = staged["contexts"]
        drafts[k], plans[k], rejections[k] = staged, plan, rejected
        ids.append(k)
    if not ids:
        raise ValueError("No isolated unsupported property targets")
    result = PropertyScopeReview(
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
