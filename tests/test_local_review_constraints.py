"""A backend ignoring output constraints still cannot bypass frozen review bounds."""

import copy
import json

import pytest
from test_anchored import PROVENANCE
from test_local_review import CONTRACT, example

from judicial_kg.extraction.draft import DraftError
from judicial_kg.extraction.local_review import ConstrainedLocalReview


@pytest.mark.parametrize("mutation", ["long_reason", "unknown_id", "duplicate"])
def test_frozen_review_bounds_remain_required_locally(mutation):
    doc, draft, plan, choices = example()
    key = doc["document_id"]
    protocol = ConstrainedLocalReview({key: draft}, {key: plan}, [key], {})
    array = next(a for a in choices.values() if a)
    if mutation == "long_reason":
        array[0]["reason"] = "x" * 81
    elif mutation == "unknown_id":
        identifier = next(k for k in array[0] if k.endswith("_id"))
        array[0][identifier] = "outside"
    else:
        array.append(copy.deepcopy(array[0]))
    with pytest.raises(DraftError):
        protocol.materialize(json.dumps(choices), doc, CONTRACT, PROVENANCE)


def test_short_source_conclusion_precedes_action_in_generation_schema():
    doc, draft, plan, _ = example()
    key = doc["document_id"]
    protocol = ConstrainedLocalReview({key: draft}, {key: plan}, [key], {})
    before = copy.deepcopy(plan)
    schema = protocol.response_format(doc, None)["schema"]
    for kind in ("context", "mention", "property", "actor"):
        array = schema["properties"][kind + "_choices"]
        assert array["minItems"] == array["maxItems"] == len(plan[kind + "_items"])
        assert list(array["items"]["properties"])[1:3] == ["reason", "action"]
    assert protocol.plans[key] == before
