"""Re-read selected byte ranges on the source host; stdin contains metadata only."""

import hashlib
import json
import sys
from pathlib import Path


def replay(root: Path, references: list[dict]) -> dict:
    checked = 0
    for ref in references:
        path = (root / ref["file"]).resolve(strict=True)
        if not path.is_relative_to(root.resolve()):
            raise ValueError("Source path escapes root")
        start, end = ref["byte_start"], ref["byte_end"]
        if (
            type(start) is not int
            or type(end) is not int
            or not 0 <= start < end <= path.stat().st_size
        ):
            raise ValueError("Invalid byte range")
        if end - start > 2_000_000:
            raise ValueError("Record exceeds replay size limit")
        with path.open("rb") as handle:
            handle.seek(start)
            raw = handle.read(end - start)
        if hashlib.sha256(raw).hexdigest() != ref["raw_record_sha256"]:
            raise ValueError("Raw record replay hash mismatch")
        row = json.loads(raw)
        if hashlib.sha256(row["text"].encode()).hexdigest() != ref["text_sha256"]:
            raise ValueError("Decoded text replay hash mismatch")
        checked += 1
    if not checked:
        raise ValueError("No source references supplied")
    return {
        "source_records_replayed": checked,
        "raw_record_hashes_matched": checked,
        "decoded_text_hashes_matched": checked,
        "full_shard_hash_verified": False,
        "semantic_correctness_evaluated": False,
    }


if __name__ == "__main__":
    payload = json.load(sys.stdin)
    print(json.dumps(replay(Path(payload["root"]), payload["references"]), indent=2))
