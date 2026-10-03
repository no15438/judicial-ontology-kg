"""Read pinned Git blobs and emit annotation inventory counts, never case text.

Usage: python scripts/audit_legacy_annotations.py --repo /path/to/judicial-graphrag
"""

import argparse
import csv
import hashlib
import io
import json
import subprocess
from pathlib import Path

COMMIT = "d4614083e72e8d4f4d6543b818e1f8935634fec7"


def inventory(repo: Path) -> dict:
    blobs = []

    def read(path: str) -> bytes:
        raw = subprocess.check_output(["git", "show", f"{COMMIT}:{path}"], cwd=repo)
        blobs.append({"path": path, "bytes": len(raw), "sha256": hashlib.sha256(raw).hexdigest()})
        return raw

    ground_truth = json.loads(read("data/annotations/ljp_ground_truth.json"))
    qa = {}
    for name in ["qa_v0", "qa_v1", "qa_v2"]:
        data = json.loads(read(f"data/annotations/{name}.json"))
        qa[name] = {
            "questions": len(data["items"]),
            "fields": sorted({key for row in data["items"] for key in row}),
        }
    reviews = {}
    for name in ["qa_v1", "qa_v2"]:
        raw = read(f"data/processed/eval/{name}/{name}_human_review.csv")
        rows = list(csv.DictReader(io.StringIO(raw.decode("utf-8"))))
        reviews[name] = {
            "rows": len(rows),
            "unique_question_ids": len({row["id"] for row in rows}),
            "modes": sorted({row["mode"] for row in rows}),
            "nonempty_human_fields": {
                field: sum(bool(row[field].strip()) for row in rows)
                for field in ["human_correctness", "human_hallucination", "human_notes"]
            },
            "reviewer_identity_independently_verified": False,
        }
    sample = json.loads(read("data/processed/eval/event_layer/event_annotation_sample.json"))
    event_paths = subprocess.check_output(
        ["git", "ls-tree", "-r", "--name-only", COMMIT, "data/processed/eval/event_layer"],
        cwd=repo,
        text=True,
    ).splitlines()
    # Static source inventory only: do not import or execute these old modules.
    for path in [
        "src/preprocess/build_raw_from_ljp.py",
        "src/evaluation/build_qa_v2.py",
        "src/evaluation/build_event_annotation.py",
        "src/evaluation/make_human_review_sheet.py",
        "data/processed/eval/qa_v2/qa_v2_human_review_README.md",
    ]:
        read(path)
    return {
        "legacy_commit": COMMIT,
        "scope": "Git-tracked metadata and source inspection; no Spark access or raw-data audit",
        "ground_truth": {
            "records": len(ground_truth),
            "record_fields": sorted({key for row in ground_truth.values() for key in row}),
        },
        "qa": qa,
        "reviews": reviews,
        "event_sample": {
            "case_ids": len(sample["case_ids"]),
            "tracked_files": event_paths,
            "event_annotation_csv_tracked": (
                "data/processed/eval/event_layer/event_annotation.csv" in event_paths
            ),
        },
        "source_files": blobs,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repo", type=Path, required=True)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    result = json.dumps(inventory(args.repo), ensure_ascii=False, indent=2) + "\n"
    if args.output:
        args.output.write_text(result, encoding="utf-8")
    else:
        print(result, end="")


if __name__ == "__main__":
    main()
