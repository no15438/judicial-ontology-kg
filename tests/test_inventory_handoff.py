"""Compiled identity inventories must survive the next stage without losing evidence."""

import copy

import pytest
from test_anchored import PROVENANCE, source
from test_scoped import CONTRACT, fixtures

from judicial_kg.extraction import context_first, inventory_handoff, scoped
from judicial_kg.extraction.draft import DraftError


def header():
    value = fixtures()
    mention = copy.deepcopy(value["actors"][0]["mentions"][0])
    mention.update(id="additional", kind="description")
    mention["evidence"] = {"unit_id": "u2", "text": "C", "occurrence": 0}
    value["actors"][0]["mentions"].append(mention)
    return value


def test_compiled_inventory_reaches_body_with_all_mentions_and_no_extra_model_call():
    value = header()
    before = copy.deepcopy(value)
    saved = {}

    class ReachedBody(Exception):
        pass

    def body(*args):
        assert args[2]["actors"] == before["actors"]
        raise ReachedBody

    with pytest.raises(ReachedBody):
        scoped.extract(
            source(),
            "",
            CONTRACT,
            PROVENANCE,
            lambda *args: pytest.fail("No inference expected in this handoff test"),
            saved.__setitem__,
            generate_header=lambda *args: value,
            generate_body=body,
            header_schema_builder=inventory_handoff.draft_schema,
        )
    assert saved["header-draft"] == before == value
    assert saved["stage-plan"]["contexts"]
    with pytest.raises(DraftError):
        context_first.check_schema(value, scoped.draft_schema(CONTRACT))


@pytest.mark.parametrize(
    "change", ["empty_mentions", "empty_contexts", "generated_events", "bad_span", "overlap"]
)
def test_handoff_preserves_schema_source_and_context_rejections(change):
    value = header()
    if change == "empty_mentions":
        value["actors"][0]["mentions"] = []
    elif change == "empty_contexts":
        value["contexts"] = []
    elif change == "generated_events":
        value["contexts"][0]["events"] = [{}]
    elif change == "bad_span":
        value["actors"][0]["mentions"][-1]["evidence"]["text"] = "not present"
    else:
        value["contexts"][0]["assertion_units"] = ["u0", "u1"]
    with pytest.raises(DraftError):
        context_first.check_schema(value, inventory_handoff.draft_schema(CONTRACT))
        scoped.compile_checked(value, source(), CONTRACT, PROVENANCE)
