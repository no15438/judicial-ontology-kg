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
        choices=["doctor", "verify-sources", "prepare-development", "validate-extractions"],
    )
    parser.add_argument("--root", type=Path, default=Path.cwd())
    parser.add_argument("--output", type=Path)
    parser.add_argument("--spark-capture", type=Path)
    parser.add_argument("--documents", type=Path, action="append")
    parser.add_argument("--candidates", type=Path)
    args = parser.parse_args()
    try:
        verified = verify_manifest(args.root)
        if args.command == "validate-extractions":
            from judicial_kg.validation_run import run_validation

            if not args.documents or args.candidates is None:
                parser.error("validate-extractions requires --documents and --candidates")
            output = args.output or args.root / "artifacts/contract-validation"
            result = run_validation(args.root, args.documents, args.candidates, output)
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
                "graph_pipeline": "source/contracts implemented; semantic graph not implemented",
            },
            indent=2,
        )
    )
    return 0 if all(checks.values()) else 1
