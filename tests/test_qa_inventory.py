"""Metadata-only checks: these fixtures are not invented case material."""

import copy
import json

import pytest

from judicial_kg.sources.qa_inventory import digest, sample_summary


def metadata_row(parent="parent-a", qa_id="qa-a"):
    text = "METADATA_ONLY_SENTINEL"
    pair = {"field_key": "metadata", "source_basis": "test", "review_status": "claimed"}
    return {
        "id": qa_id,
        "text": text,
        "source_detail": {"source_id": parent, "standardized_text_hash": digest(text.encode())},
        "content": {
            "text_sha256": digest(text.encode()),
            "raw_text_sha256": "unverified-raw-hash",
            "text_length": len(text),
        },
        "qa_pairs": [pair],
        "annotation": {"qa_pairs": [copy.deepcopy(pair)]},
        "data_content": [{"media_type": ["text"], "content": text}],
        "human_review_status": "人工已核验",
        "label_info": {"label_method": "model-prelabel", "annotator": "行业领域专家"},
        "license": "公共授权",
        "generated_flag": False,
        "generated_data_indicator": 0,
        "qa_constructed_flag": True,
        "case_type": "metadata-only",
    }


def test_count_parent_groups_without_merging_identical_text_or_exporting_content():
    result = sample_summary(
        [metadata_row(), metadata_row(qa_id="qa-b"), metadata_row("parent-b", "qa-c")]
    )
    assert result["records"] == 3
    assert result["unique_parent_source_ids"] == 2
    assert result["unique_texts"] == 1
    assert result["qa_rows_per_parent"] == [1, 2]
    assert len(result["exposed_parent_text_keys"]) == 2
    assert "METADATA_ONLY_SENTINEL" not in json.dumps(result)
    assert "parent-a" not in json.dumps(result)


def test_declared_review_and_authorization_do_not_admit_gold():
    result = sample_summary([metadata_row()])
    assert result["gold_admitted"] is False
    assert result["human_review_independently_verified"] is False
    assert result["checks_passing_rows"]["text_sha256_matches"] == 1
    assert result["checks_passing_rows"]["full_text_mirror_matches"] == 1


def test_tampered_text_and_annotation_stay_in_denominator():
    row = metadata_row()
    row["text"] = "CHANGED_METADATA_ONLY"
    row["annotation"]["qa_pairs"] = []
    result = sample_summary([row])
    assert result["records"] == 1
    for check in [
        "text_sha256_matches",
        "full_text_mirror_matches",
        "qa_annotation_mirror_matches",
    ]:
        assert result["checks_passing_rows"][check] == 0


@pytest.mark.parametrize("rows", [[], {}])
def test_empty_or_nonarray_sample_cannot_pass(rows):
    with pytest.raises(ValueError, match="nonempty"):
        sample_summary(rows)


def test_missing_parent_cannot_create_an_anonymous_group():
    row = metadata_row()
    row["source_detail"]["source_id"] = ""
    with pytest.raises(ValueError, match="parent source_id"):
        sample_summary([row])
