"""Offline environment and imported-source checks, not a graph extraction command."""

import argparse
import json
import shutil
import sys
from pathlib import Path

from judicial_kg.provenance import verify_manifest


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=["doctor", "verify-sources"])
    parser.add_argument("--root", type=Path, default=Path.cwd())
    args = parser.parse_args()
    try:
        verified = verify_manifest(args.root)
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
                "graph_pipeline": "not implemented",
            },
            indent=2,
        )
    )
    return 0 if all(checks.values()) else 1
