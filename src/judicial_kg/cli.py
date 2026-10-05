"""Source preparation and extraction contracts, not semantic graph evaluation."""

import argparse
import json
import shutil
import sys
from pathlib import Path

from judicial_kg.provenance import verify_manifest


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "command",
        choices=[
            "doctor",
            "verify-sources",
            "prepare-development",
            "validate-extractions",
            "extract-qwen",
            "extract-bailian",
            "replay-qwen",
            "repair-qwen-mentions",
            "replay-qwen-mentions",
            "repair-qwen-selectors",
            "replay-qwen-selectors",
            "repair-qwen-attribution",
            "replay-qwen-attribution",
            "assemble-graph",
            "stage-qwen-selectors",
            "repair-qwen-local",
            "repair-qwen-property-scope",
            "replay-qwen-property-scope",
            "repair-qwen-actor-type",
            "replay-qwen-actor-type",
            "replay-qwen-local",
            "repair-qwen-context-scope",
            "replay-qwen-context-scope",
        ],
    )
    parser.add_argument("--root", type=Path, default=Path.cwd())
    parser.add_argument("--output", type=Path)
    parser.add_argument("--spark-capture", type=Path)
    parser.add_argument("--documents", type=Path, action="append")
    parser.add_argument("--candidates", type=Path)
    parser.add_argument("--config", type=Path)
    parser.add_argument("--local-run", type=Path)
    parser.add_argument("--source-run", type=Path)
    parser.add_argument("--repair-run", type=Path)
    parser.add_argument("--repair-spec", type=Path)
    parser.add_argument(
        "--contract-version",
        choices=["0.1.0", "0.2.0"],
        default="0.1.0",
        help="Contract for offline validation and graph assembly",
    )
    parser.add_argument("--execute", action="store_true", help="Submit approved cloud requests")
    args = parser.parse_args()
    try:
        verified = verify_manifest(args.root)
        if args.command in {"repair-qwen-context-scope", "replay-qwen-context-scope"}:
            from judicial_kg.extraction import context_scope_review as module

            if not all((args.documents, args.repair_spec, args.output)):
                parser.error("context scope review requires documents, repair-spec and output")
            if args.command.startswith("replay-"):
                if args.repair_run is None:
                    parser.error("context scope replay requires repair-run")
                result = module.replay_repair(
                    args.root, args.documents, args.repair_spec, args.repair_run, args.output
                )
                failed = not (
                    result["accepted_candidates_match_saved"] and result["outcomes_match_saved"]
                )
            else:
                result = module.run_repair(
                    args.root,
                    args.documents,
                    args.repair_spec,
                    args.config or args.root / ("configs/" + module.VERSION + ".json"),
                    args.output,
                )
                failed = any(o["issues"] for o in result["selected_outcomes"])
            print(json.dumps(result, ensure_ascii=False, indent=2))
            return int(failed)
        if args.command in {
            "repair-qwen-actor-type",
            "replay-qwen-actor-type",
            "repair-qwen-property-scope",
            "replay-qwen-property-scope",
        }:
            from judicial_kg.extraction import actor_type_review, property_scope_review

            module = (
                property_scope_review
                if args.command.endswith("property-scope")
                else actor_type_review
            )

            if not all(
                (args.documents, args.repair_spec, args.source_run, args.local_run, args.output)
            ):
                parser.error(
                    "actor type review requires documents, spec, selector/local source runs, output"
                )
            if args.command.startswith("replay-"):
                if args.repair_run is None:
                    parser.error("actor type replay requires repair-run")
                result = module.replay_repair(
                    args.root,
                    args.documents,
                    args.repair_spec,
                    args.source_run,
                    args.local_run,
                    args.repair_run,
                    args.output,
                )
                failed = not (
                    result["accepted_candidates_match_saved"] and result["outcomes_match_saved"]
                )
            else:
                result = module.run_repair(
                    args.root,
                    args.documents,
                    args.repair_spec,
                    args.source_run,
                    args.local_run,
                    args.config or args.root / ("configs/" + module.VERSION + ".json"),
                    args.output,
                )
                failed = any(o["issues"] for o in result["selected_outcomes"])
            print(json.dumps(result, ensure_ascii=False, indent=2))
            return int(failed)
        if args.command == "stage-qwen-selectors":
            from judicial_kg.extraction.selector_stage import prepare_stages

            if not all((args.documents, args.repair_spec, args.repair_run, args.output)):
                parser.error("stage requires documents, repair-spec, repair-run and new output")
            result = prepare_stages(
                args.root, args.documents, args.repair_spec, args.repair_run, args.output
            )
            print(json.dumps(result, ensure_ascii=False, indent=2))
            return int(result["document_status_counts"].get("unresolved", 0) > 0)
        if args.command in {
            "repair-qwen-selectors",
            "replay-qwen-selectors",
            "repair-qwen-attribution",
            "replay-qwen-attribution",
            "repair-qwen-local",
            "replay-qwen-local",
        }:
            from judicial_kg.extraction import attribution_repair, local_review, selector_repair

            attribution = args.command.endswith(("attribution", "local"))
            module = (
                local_review
                if args.command.endswith("local")
                else attribution_repair
                if attribution
                else selector_repair
            )
            if attribution and args.source_run is None:
                parser.error("attribution repair requires parent selector source-run")
            extra = [args.source_run] if attribution else []

            if not all((args.documents, args.repair_spec, args.output)):
                parser.error("selector repair requires documents, repair-spec and new output")
            if args.command.startswith("replay-"):
                if args.repair_run is None:
                    parser.error("selector replay requires repair-run")
                result = module.replay_repair(
                    args.root,
                    args.documents,
                    args.repair_spec,
                    *extra,
                    args.repair_run,
                    args.output,
                )
                successful = (
                    result["accepted_candidates_match_saved"] and result["outcomes_match_saved"]
                )
            else:
                result = module.run_repair(
                    args.root,
                    args.documents,
                    args.repair_spec,
                    *extra,
                    args.config or args.root / ("configs/" + module.VERSION + ".json"),
                    args.output,
                )
                successful = (
                    result["document_status_counts"].get("accepted_candidate", 0)
                    == result["selected_documents"]
                )
            print(json.dumps(result, ensure_ascii=False, indent=2))
            return int(not successful)
        if args.command == "assemble-graph":
            from judicial_kg.graph.run import run_assembly

            if not args.documents or args.candidates is None or args.output is None:
                parser.error("assemble-graph requires --documents, --candidates and a new --output")
            result = run_assembly(
                args.root, args.documents, args.candidates, args.output, args.contract_version
            )
            print(json.dumps(result, ensure_ascii=False, indent=2))
            validation = result["validation"]
            return int(
                bool(
                    validation["attempts_rejected"]
                    or validation["documents_missing"]
                    or result["documents_assembly_failed"]
                )
            )
        if args.command == "replay-qwen-mentions":
            from judicial_kg.extraction.repair_replay import replay_repairs

            if not all(
                (args.documents, args.source_run, args.repair_run, args.candidates, args.output)
            ):
                parser.error(
                    "repair replay requires documents, source/repair runs, candidates and output"
                )
            result = replay_repairs(
                args.root,
                args.documents,
                args.source_run,
                args.candidates,
                args.repair_run,
                args.output,
            )
            print(json.dumps(result, ensure_ascii=False, indent=2))
            return int(
                not (result["accepted_candidates_match_saved"] and result["outcomes_match_saved"])
            )
        if args.command == "repair-qwen-mentions":
            from judicial_kg.extraction.mention_repair import run_repair

            if not all((args.documents, args.source_run, args.candidates, args.output)):
                parser.error("repair requires --documents, --source-run, --candidates and --output")
            result = run_repair(
                args.root,
                args.documents,
                args.source_run,
                args.candidates,
                args.config or args.root / "configs/qwen-mention-repair-v5.json",
                args.output,
            )
            print(json.dumps(result, ensure_ascii=False, indent=2))
            return int(
                result["document_status_counts"].get("accepted_candidate", 0)
                != result["selected_documents"]
            )
        if args.command == "replay-qwen":
            from judicial_kg.extraction.replay import replay_completed

            if not args.documents or args.source_run is None or args.output is None:
                parser.error("replay-qwen requires --documents, --source-run and a new --output")
            result = replay_completed(args.root, args.documents, args.source_run, args.output)
            print(json.dumps(result, ensure_ascii=False, indent=2))
            accepted = sum(
                result["document_status_counts"].get(k, 0)
                for k in ("accepted_candidate", "accepted_empty")
            )
            return int(accepted != result["selected_documents"])
        if args.command in {"extract-qwen", "extract-bailian"}:
            from datetime import UTC, datetime
            from uuid import uuid4

            if not args.documents:
                parser.error("extraction requires --documents")
            run_name = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ") + "-" + uuid4().hex[:8]
            provider = "bailian" if args.command == "extract-bailian" else "qwen"
            output = args.output or args.root / f"artifacts/{provider}-extraction" / run_name
            options = {}
            if provider == "bailian":
                from judicial_kg.extraction.bailian import run_bailian as run_extraction

                options["execute"] = args.execute
            else:
                from judicial_kg.extraction.run import run_extraction
            result = run_extraction(
                args.root,
                args.documents,
                args.config or args.root / f"configs/{provider}-extraction.json",
                output,
                **options,
            )
            print(
                json.dumps(
                    {"output": str(output.resolve()), **result}, ensure_ascii=False, indent=2
                )
            )
            accepted = sum(
                result["document_status_counts"].get(k, 0)
                for k in ("accepted_candidate", "accepted_empty", "planned_not_submitted")
            )
            return int(accepted != result["selected_documents"])
        if args.command == "validate-extractions":
            from judicial_kg.validation_run import run_validation

            if not args.documents or args.candidates is None:
                parser.error("validate-extractions requires --documents and --candidates")
            output = args.output or args.root / "artifacts/contract-validation"
            result = run_validation(
                args.root, args.documents, args.candidates, output, args.contract_version
            )
            print(json.dumps(result, ensure_ascii=False, indent=2))
            return int(bool(result["attempts_rejected"] or result["documents_missing"]))
        if args.command == "prepare-development":
            from judicial_kg.sources.development import prepare

            output = args.output or args.root / "artifacts/source-development"
            result = prepare(args.root, output, args.spark_capture)
            print(
                json.dumps(
                    {
                        "output": str(output.resolve()),
                        "appealcase_development_documents": result["appealcase"]["development"][
                            "propagated_documents"
                        ],
                        "spark_development_documents": (
                            result["spark"]["normalized_documents"] if result["spark"] else None
                        ),
                        "gold_available": False,
                        "requires_user_legal_annotation": False,
                    },
                    ensure_ascii=False,
                    indent=2,
                )
            )
            return 0
    except (OSError, ValueError, KeyError, TypeError) as error:
        print(f"Source check failed: {error}", file=sys.stderr)
        return 1
    if args.command == "verify-sources":
        print(f"Verified {len(verified)} imported metadata/reference files; no raw corpus checked.")
        return 0
    checks = {
        "python_3_12": sys.version_info[:2] == (3, 12),
        "git_available": shutil.which("git") is not None,
        "virtual_environment": sys.prefix != sys.base_prefix,
        "local_data_directory": (args.root / "data/raw").is_dir(),
        "local_artifacts_directory": (args.root / "artifacts").is_dir(),
    }
    print(
        json.dumps(
            {
                "checks": checks,
                "verified_reference_files": len(verified),
                "raw_corpus": "not checked",
                "model_services": "not configured or probed by this command",
                "graph_pipeline": (
                    "source/contracts/candidate assembly implemented; semantics not assessed"
                ),
            },
            indent=2,
        )
    )
    return 0 if all(checks.values()) else 1
