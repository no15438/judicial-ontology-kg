"""Bounded read-only development sampling of one-object-per-line JSON arrays.

Run via SSH stdin. Stdout includes selected source rows: redirect it to an ignored
local artifact, never to a committed file or a chat transcript. No remote writes.
"""

import argparse
import hashlib
import json
from collections import Counter
from pathlib import Path


class ScanBudget(Exception):
    def __init__(self, reason, bytes_read):
        self.reason = reason
        self.bytes_read = bytes_read


def array_rows(path: Path, max_bytes: int, max_record_bytes: int = 2_000_000):
    """Strict line-array format with byte positions; never load an entire shard."""
    with path.open("rb") as stream:
        opening = stream.readline(min(100, max_bytes + 1))
        if stream.tell() > max_bytes:
            raise ScanBudget("byte_budget", stream.tell())
        if opening.strip() != b"[":
            raise ValueError("Expected array opening on its own line")
        index, preceding_comma = 0, False
        while True:
            offset = stream.tell()
            if offset >= max_bytes:
                raise ScanBudget("byte_budget", offset)
            line = stream.readline(min(max_record_bytes + 1, max_bytes - offset + 1))
            if stream.tell() > max_bytes:
                raise ScanBudget("byte_budget", stream.tell())
            if len(line) > max_record_bytes:
                raise ValueError("Source record exceeds bounded parser limit")
            if not line:
                raise ValueError("Truncated JSON array")
            stripped = line.strip()
            if not stripped:
                continue
            if stripped == b"]":
                if preceding_comma:
                    raise ValueError("Trailing comma in source array")
                # Do not silently accept trailing non-JSON material.
                while tail := stream.read(min(4096, max_bytes - stream.tell() + 1)):
                    if stream.tell() > max_bytes:
                        raise ScanBudget("byte_budget", stream.tell())
                    if tail.strip():
                        raise ValueError("Content after JSON array")
                return stream.tell()
            if index and not preceding_comma:
                raise ValueError("Missing source array delimiter")
            preceding_comma = stripped.endswith(b",")
            payload = stripped[:-1] if preceding_comma else stripped
            row = json.loads(payload)
            if not isinstance(row, dict):
                raise ValueError("Expected source record object")
            start = offset + len(line) - len(line.lstrip())
            yield (
                row,
                {
                    "record_pointer": f"/{index}",
                    "byte_start": start,
                    "byte_end": start + len(payload),
                    "prefix_bytes_read": stream.tell(),
                    "raw_record_sha256": hashlib.sha256(payload).hexdigest(),
                },
            )
            index += 1


def scan(root: Path, max_parents: int, max_rows: int, max_bytes: int) -> dict:
    if min(max_parents, max_rows, max_bytes) <= 0:
        raise ValueError("Scan budgets must be positive")
    manifest_raw = (root / "manifests/shard_manifest.json").read_bytes()
    manifest = json.loads(manifest_raw)
    names = [Path(shard["remote"]).name for shard in manifest["shards"]]
    if not names or len(names) != len(set(names)):
        raise ValueError("Expected nonempty unique shard list")
    selected, text_hashes = [], {}
    inspected, consumed = 0, 0
    case_types, scanned_files = Counter(), []
    stop_reason = "listed_files_exhausted"
    for shard in manifest["shards"]:
        relative = "data/" + Path(shard["remote"]).name
        path = (root / relative).resolve(strict=True)
        if not path.is_relative_to(root.resolve()):
            raise ValueError("Shard path escapes source root")
        prefix_bytes = 0
        scanned_files.append(
            {"path": relative, "declared_sha256": shard["sha256"], "file_hash_verified": False}
        )
        iterator = array_rows(path, max_bytes - consumed)
        try:
            while True:
                try:
                    row, locator = next(iterator)
                except StopIteration as done:
                    prefix_bytes = done.value
                    break
                prefix_bytes = locator["prefix_bytes_read"]
                inspected += 1
                parent = row["source_detail"]["source_id"]
                if not isinstance(parent, str) or not parent:
                    raise ValueError("Missing parent source identity")
                case_types[row["case_type"]] += 1
                if len(text_hashes) >= max_parents and parent not in text_hashes:
                    stop_reason = "parent_budget_after_boundary"
                    break
                if row["case_type"] == "民间借贷纠纷":
                    text_hash = hashlib.sha256(row["text"].encode()).hexdigest()
                    if text_hash != row["content"]["text_sha256"]:
                        raise ValueError("Selected source text hash mismatch")
                    if parent in text_hashes and text_hashes[parent] != text_hash:
                        raise ValueError("Same parent source ID has conflicting text")
                    text_hashes[parent] = text_hash
                    selected.append(
                        {
                            "file": relative,
                            "declared_file_sha256": shard["sha256"],
                            "file_hash_verified": False,
                            **locator,
                            "row": row,
                        }
                    )
                if inspected >= max_rows:
                    stop_reason = "row_budget"
                    break
            if stop_reason == "listed_files_exhausted":
                consumed += prefix_bytes
                scanned_files[-1]["prefix_bytes_read"] = prefix_bytes
                continue
        except ScanBudget as error:
            stop_reason, prefix_bytes = error.reason, error.bytes_read
        finally:
            iterator.close()
        consumed += prefix_bytes
        scanned_files[-1]["prefix_bytes_read"] = prefix_bytes
        break
    return {
        "schema_version": 1,
        "source_root": str(root),
        "manifest_sha256": hashlib.sha256(manifest_raw).hexdigest(),
        "scope": "Ordered bounded development scan, not a random or full-corpus audit",
        "budgets": {"parents": max_parents, "rows": max_rows, "bytes": max_bytes},
        "stop_reason": stop_reason,
        "inspected_rows": inspected,
        "prefix_bytes_read": consumed,
        "observed_case_type_rows": dict(case_types),
        "selected_parents": len(text_hashes),
        "selected_qa_rows": len(selected),
        "scanned_files": scanned_files,
        "gold_available": False,
        "selected_rows": selected,
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, required=True)
    parser.add_argument("--max-parents", type=int, default=12)
    parser.add_argument("--max-rows", type=int, default=5000)
    parser.add_argument("--max-bytes", type=int, default=128_000_000)
    args = parser.parse_args()
    print(
        json.dumps(
            scan(args.root, args.max_parents, args.max_rows, args.max_bytes), ensure_ascii=False
        )
    )


if __name__ == "__main__":
    main()
