"""Pure metadata fixtures verify coordinates and identity, never legal correctness."""

import copy

import pytest

from judicial_kg.sources.appealcase import Groups, load_verified, normalized_docket
from judicial_kg.sources.development import safe_output
from judicial_kg.sources.records import document, evidence_span, pointer_get, verify_evidence


def example():
    return document("metadata", {"record_pointer": "/0"}, {"field": ("/0/field", "元数据🙂\nABC")})


def test_unicode_offsets_replay_without_normalization():
    doc = example()
    span = evidence_span(doc, "field", 2, 5)
    assert span["quote"] == "据🙂\n"
    verify_evidence(doc, span)
    assert (
        pointer_get([{"field": doc["fields"][0]["text"]}], span["source_pointer"])[2:5]
        == span["quote"]
    )


@pytest.mark.parametrize("start,end", [(-1, 1), (0, 99), (1, 1), (2, 1), (True, 2), (0, 1.5)])
def test_invalid_evidence_ranges_are_rejected(start, end):
    with pytest.raises(ValueError, match="range"):
        evidence_span(example(), "field", start, end)


def test_evidence_cannot_cross_document_or_changed_source():
    doc = example()
    span = evidence_span(doc, "field", 0, 2)
    other = document("metadata", {"record_pointer": "/1"}, {"field": ("/1/field", "元数据🙂\nABC")})
    with pytest.raises(ValueError, match="named document"):
        verify_evidence(other, span)
    doc["fields"][0]["text"] = "changed"
    with pytest.raises(ValueError, match="changed"):
        verify_evidence(doc, span)


def test_same_name_fields_do_not_silently_choose_first():
    doc = example()
    doc["fields"].append(copy.deepcopy(doc["fields"][0]))
    with pytest.raises(ValueError, match="exactly once"):
        evidence_span(doc, "field", 0, 1)


def test_reference_metadata_cannot_promote_to_gold():
    with pytest.raises(ValueError, match="override"):
        document("metadata", {}, {"field": ("/field", "metadata")}, reference_status="gold")


@pytest.mark.parametrize("pointer", ["/-1", "/01", "/~2"])
def test_invalid_pointers_are_not_reinterpreted(pointer):
    with pytest.raises(ValueError, match="pointer"):
        pointer_get(["metadata"], pointer)


def test_pointer_escaping_and_group_transitivity():
    assert pointer_get({"a/b": {"~": ["metadata"]}}, "/a~1b/~0/0") == "metadata"
    groups = Groups(5)
    groups.link(4, 2)
    groups.link(1, 2)
    groups.link(0, 1)
    assert len({groups.root(i) for i in [0, 1, 2, 4]}) == 1
    assert groups.root(3) != groups.root(0)
    assert normalized_docket("（ ＭＥＴＡ ）") == "(META)"


def test_pinned_source_hash_mismatch_is_fatal(tmp_path):
    source = tmp_path / "metadata.json"
    source.write_text("[]")
    with pytest.raises(ValueError, match="hash mismatch"):
        load_verified(source, "0" * 64)


def test_text_outputs_cannot_be_written_to_tracked_or_external_paths(tmp_path):
    root = tmp_path / "repo"
    root.mkdir()
    assert safe_output(root, root / "artifacts/run") == root / "artifacts/run"
    for output in [root / "research", tmp_path / "external"]:
        with pytest.raises(ValueError, match="must stay"):
            safe_output(root, output)
    (root / "artifacts").symlink_to(tmp_path)
    with pytest.raises(ValueError, match="must stay"):
        safe_output(root, root / "artifacts/leak")
