"""Verify imported research metadata without downloading or reading case corpora."""

import hashlib
import json
from pathlib import Path


def verify_manifest(root: Path) -> list[str]:
    """Fail closed on missing, changed, duplicate, or out-of-workspace source entries."""
    root = root.resolve()
    manifest = json.loads((root / "research/sources.json").read_text(encoding="utf-8"))
    if manifest.get("schema_version") != 1 or not manifest.get("files"):
        raise ValueError("Expected a nonempty version 1 source manifest")
    verified = []
    seen = set()
    for entry in manifest["files"]:
        relative = entry["path"]
        candidate = Path(relative)
        path = (root / candidate).resolve()
        if candidate.is_absolute() or not path.is_relative_to(root):
            raise ValueError(f"Source path escapes workspace: {relative}")
        if path in seen:
            raise ValueError(f"Duplicate source path: {relative}")
        seen.add(path)
        raw = path.read_bytes()
        if len(raw) != entry["bytes"] or hashlib.sha256(raw).hexdigest() != entry["sha256"]:
            raise ValueError(f"Source integrity mismatch: {relative}")
        verified.append(relative)
    return verified
