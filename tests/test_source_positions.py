import pytest

from judicial_kg.extraction.anchored import source_units
from judicial_kg.extraction.draft import DraftError
from judicial_kg.extraction.source_positions import SourcePositionRouter, resolve_forms
from judicial_kg.sources.records import document


def source(text):
    return document(
        "metadata",
        {"record_pointer": "/0"},
        {"text": ("/0/text", text)},
        family_id="metadata:0",
        allocation="development_exposed",
    )


@pytest.mark.parametrize(
    "text,form,status",
    [
        ("A\u3000B A\u3000B", "A B", "space_mapped"),
        ("A  B", "A B", "space_mapped"),
        ("A B A\u3000B", "A B", "ambiguous_source_variants"),
        ("A\nB", "A B", "missing"),
        ("A B", "AB", "missing"),
        ("AB", "A B", "missing"),
        ("A\tB", "A B", "missing"),
        ("A，B", "A,B", "missing"),
    ],
)
def test_mapping_only_horizontal_spaces(text, form, status):
    doc = source(text)
    mapped, ledger = resolve_forms(doc, source_units(doc), {"forms": [form]})
    assert ledger[0]["status"] == status
    for span in ledger[0]["candidates"]:
        assert text[span["start"] : span["end"]] == span["quote"]
    if status == "space_mapped":
        assert mapped["forms"] == [ledger[0]["candidates"][0]["quote"]]


def test_router_preserves_positions_and_proposal_lineage():
    doc = source("A\u3000B A\u3000B")
    saved = {}

    def infer(messages, fmt, stage):
        return '{"p0":"actor_candidate"}'

    router = SourcePositionRouter(doc, infer, saved.__setitem__)
    result = router.route(
        {"forms": ["A B", "A\u3000B"]}, [u["id"] for u in source_units(doc)], "inventory"
    )
    assert result == {"forms": ["A\u3000B"]}
    ledger = saved["inventory-source-position-map"]["ledger"]
    assert len(ledger) == 2
    assert [r["output_index"] for r in ledger] == [0, 0]
    assert len(ledger[0]["candidates"]) == 2


def test_ambiguity_stops_before_model_call():
    doc = source("A B A\u3000B")

    def infer(*args):
        pytest.fail("Ambiguous source must not reach model")

    router = SourcePositionRouter(doc, infer, lambda *args: None)
    with pytest.raises(DraftError, match="source_form_mapping_ambiguous"):
        router.route({"forms": ["A B"]}, [u["id"] for u in source_units(doc)], "inventory")
