"""Integrity failures must stop verification without needing any case text."""

import hashlib
import json

import pytest

from judicial_kg.provenance import verify_manifest


def make_workspace(tmp_path):
    research = tmp_path / "research"
    research.mkdir()
    source = research / "reference.json"
    raw = b'{"dataset_id": "metadata-only-test"}\n'
    source.write_bytes(raw)
    manifest = {
        "schema_version": 1,
        "files": [
            {
                "path": "research/reference.json",
                "bytes": len(raw),
                "sha256": hashlib.sha256(raw).hexdigest(),
            }
        ],
    }
    target = research / "sources.json"
    target.write_text(json.dumps(manifest), encoding="utf-8")
    return source, target, manifest


def test_intact_reference_then_same_size_tampering(tmp_path):
    source, _, _ = make_workspace(tmp_path)
    assert verify_manifest(tmp_path) == ["research/reference.json"]
    source.write_bytes(source.read_bytes().replace(b"test", b"fake"))
    with pytest.raises(ValueError, match="integrity mismatch"):
        verify_manifest(tmp_path)


def test_missing_reference_is_failure(tmp_path):
    source, _, _ = make_workspace(tmp_path)
    source.unlink()
    with pytest.raises(FileNotFoundError):
        verify_manifest(tmp_path)


@pytest.mark.parametrize("path", ["../outside.json", "/tmp/outside.json"])
def test_manifest_cannot_read_outside_workspace(tmp_path, path):
    _, target, manifest = make_workspace(tmp_path)
    manifest["files"][0]["path"] = path
    target.write_text(json.dumps(manifest), encoding="utf-8")
    with pytest.raises(ValueError, match="escapes workspace"):
        verify_manifest(tmp_path)


def test_symlink_cannot_escape_workspace(tmp_path):
    root = tmp_path / "repo"
    root.mkdir()
    source, _, _ = make_workspace(root)
    outside = tmp_path / "outside.json"
    outside.write_bytes(source.read_bytes())
    source.unlink()
    source.symlink_to(outside)
    with pytest.raises(ValueError, match="escapes workspace"):
        verify_manifest(root)


def test_duplicates_do_not_inflate_verified_count(tmp_path):
    _, target, manifest = make_workspace(tmp_path)
    manifest["files"] *= 2
    target.write_text(json.dumps(manifest), encoding="utf-8")
    with pytest.raises(ValueError, match="Duplicate source"):
        verify_manifest(tmp_path)


def test_empty_manifest_is_not_success(tmp_path):
    _, target, manifest = make_workspace(tmp_path)
    manifest["files"] = []
    target.write_text(json.dumps(manifest), encoding="utf-8")
    with pytest.raises(ValueError, match="nonempty"):
        verify_manifest(tmp_path)
