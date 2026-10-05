"""Discovery query duplicates must never collapse distinct source mentions or whitespace."""

import pytest

from judicial_kg.extraction import actor_inventory, unique_forms
from judicial_kg.extraction.anchored import GroundingError

SCHEMA = actor_inventory.obj(
    {"forms": {"type": "array", "uniqueItems": True, "items": {"type": "string", "minLength": 1}}}
)


def test_exact_queries_only_with_original_index_ledger():
    original = {"forms": ["A", "A", "A B", "A\u3000B", "A"]}
    result, receipt = unique_forms.deduplicate(original, SCHEMA)
    assert result == {"forms": ["A", "A B", "A\u3000B"]}
    assert receipt["source_indices"] == [[0, 1, 4], [2], [3]]
    assert len(original["forms"]) == 5
    assert receipt["all_source_occurrences_still_enumerated"]


@pytest.mark.parametrize("value", [{"forms": [1, 1]}, {"forms": [""]}, {"forms": [], "extra": 1}])
def test_other_schema_errors_not_suppressed(value):
    with pytest.raises(GroundingError):
        unique_forms.deduplicate(value, SCHEMA)
