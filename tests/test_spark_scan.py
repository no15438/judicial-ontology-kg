"""JSON and provenance fixtures contain metadata tokens, no invented judicial cases."""

import hashlib
import json

import pytest

from judicial_kg.sources.spark import adapt_scan
from judicial_kg.sources.spark_replay import replay
from judicial_kg.sources.spark_scan import ScanBudget, array_rows, scan


def row(parent="a", text="元数据🙂"):
    return {
        "source_detail": {"source_id": parent},
        "text": text,
        "content": {
            "text_sha256": hashlib.sha256(text.encode()).hexdigest(),
            "raw_text_sha256": "unknown",
        },
        "case_type": "民间借贷纠纷",
        "privacy_info": {"desensitization_method": "test-only"},
        "qa_pairs": [{"field_key": "metadata", "review_status": "claimed_review"}],
    }


def package(root, records):
    (root / "data").mkdir()
    (root / "manifests").mkdir()
    raw = (
        b"[\r\n"
        + b",\r\n".join(b"  " + json.dumps(r, ensure_ascii=False).encode() for r in records)
        + b"\r\n]\r\n"
    )
    path = root / "data/shard.json"
    path.write_bytes(raw)
    (root / "manifests/shard_manifest.json").write_text(
        json.dumps(
            {
                "shards": [
                    {"remote": "/source/data/shard.json", "sha256": hashlib.sha256(raw).hexdigest()}
                ]
            }
        )
    )
    return path


def test_byte_ranges_replay_unicode_and_parent_rows_do_not_inflate_documents(tmp_path):
    path = package(tmp_path, [row(), row(), row("b")])
    captured = scan(tmp_path, max_parents=1, max_rows=20, max_bytes=10000)
    assert captured["selected_parents"] == 1
    assert captured["selected_qa_rows"] == 2
    assert captured["inspected_rows"] == 3  # Includes the inspected boundary row.
    assert captured["stop_reason"] == "parent_budget_after_boundary"
    references = []
    for selected in captured["selected_rows"]:
        raw = path.read_bytes()[selected["byte_start"] : selected["byte_end"]]
        assert json.loads(raw) == selected["row"]
        assert hashlib.sha256(raw).hexdigest() == selected["raw_record_sha256"]
        references.append({**selected, "text_sha256": selected["row"]["content"]["text_sha256"]})
    assert replay(tmp_path, references)["source_records_replayed"] == 2
    docs, report = adapt_scan(captured)
    assert len(docs) == 1 and len(docs[0]["reference_labels"]) == 2
    assert docs[0]["source"]["file_hash_verified"] is False
    assert docs[0]["reference_status"] == "not_graph_gold"
    assert report["semantic_precision_recall_f1"] is None
    path.write_bytes(path.read_bytes().replace(b"claimed_review", b"changed_review"))
    with pytest.raises(ValueError, match="replay hash"):
        replay(tmp_path, references)


@pytest.mark.parametrize(
    "raw",
    [
        b'[{"a":1}]',
        b'[\n{"a":1},\n]\n',
        b'[\n{"a":1}\n{"b":2}\n]\n',
        b'[\n{"a":1}\n',
        b"[\n]\nextra",
        b"[\n[]\n]\n",
    ],
)
def test_invalid_or_unsupported_array_formats_fail(tmp_path, raw):
    path = tmp_path / "metadata.json"
    path.write_bytes(raw)
    with pytest.raises(ValueError):
        list(array_rows(path, 10000))


def test_byte_and_row_budgets_are_visible_not_full_scan_claims(tmp_path):
    package(tmp_path, [row(), row("b")])
    capture = scan(tmp_path, 10, 1, 10000)
    assert capture["stop_reason"] == "row_budget"
    assert capture["inspected_rows"] == 1
    capture = scan(tmp_path, 10, 10, 10)
    assert capture["stop_reason"] == "byte_budget"
    assert capture["inspected_rows"] == 0
    assert capture["prefix_bytes_read"] <= 11  # At most one lookahead byte.
    with pytest.raises(ScanBudget):
        list(array_rows(tmp_path / "data/shard.json", 1))


def test_full_scan_counts_array_boundaries_and_trailing_whitespace(tmp_path):
    path = package(tmp_path, [row()])
    capture = scan(tmp_path, 10, 10, 10000)
    assert capture["stop_reason"] == "listed_files_exhausted"
    assert capture["prefix_bytes_read"] == path.stat().st_size


def test_conflicting_parent_text_cannot_silently_merge(tmp_path):
    package(tmp_path, [row(), row(text="不同元数据")])
    with pytest.raises(ValueError, match="conflicting text"):
        scan(tmp_path, 10, 10, 10000)


def test_record_and_manifest_limits_are_explicit(tmp_path):
    path = package(tmp_path, [row()])
    with pytest.raises(ValueError, match="parser limit"):
        list(array_rows(path, 10000, max_record_bytes=10))
    with pytest.raises(ValueError, match="positive"):
        scan(tmp_path, 0, 10, 10000)
    manifest = tmp_path / "manifests/shard_manifest.json"
    data = json.loads(manifest.read_text())
    data["shards"] *= 2
    manifest.write_text(json.dumps(data))
    with pytest.raises(ValueError, match="unique shard"):
        scan(tmp_path, 10, 10, 10000)


def test_replay_paths_cannot_escape_source_root(tmp_path):
    outside = tmp_path / "metadata.json"
    outside.write_text("{}")
    root = tmp_path / "root"
    root.mkdir()
    with pytest.raises(ValueError, match="escapes"):
        replay(root, [{"file": "../metadata.json"}])
    with pytest.raises(ValueError, match="No source"):
        replay(root, [])
