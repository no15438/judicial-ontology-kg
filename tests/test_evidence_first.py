"""Evidence choices are constructed from source, never from a model paraphrase."""

import copy

import pytest
from test_anchored import source

from judicial_kg.extraction.draft import DraftError
from judicial_kg.extraction.evidence_first import build_evidence_catalog, compile_facts


def fixture():
    doc = source()
    context = {"assertion_units": ["u0", "u1"]}
    catalog = build_evidence_catalog(doc, context)
    index = next(i for i, e in enumerate(catalog["entries"]) if e["selector"]["unit_id"] == "u1")
    facts = {
        "roles": [],
        "statements": [],
        "events": [
            {
                "evidence_units": ["u1"],
                "statements": [
                    {
                        "predicate": "other",
                        "polarity": "negated",
                        "value": {"kind": "text", "source": {"span_id": f"v{index}"}},
                    }
                ],
            }
        ],
    }
    return doc, context, catalog, facts


def test_original_literal_and_polarity_survive_without_model_copying():
    doc, context, catalog, facts = fixture()
    before = copy.deepcopy(facts)
    result = compile_facts(doc, context, catalog, facts)
    statement = result["events"][0]["statements"][0]
    assert statement["value"]["source"] == {"unit_id": "u1", "text": "B 42。", "occurrence": 0}
    assert statement["polarity"] == "negated"
    assert facts == before


@pytest.mark.parametrize("failure", ["owner", "unknown", "catalog", "scope"])
def test_evidence_cannot_escape_original_source_or_owner(failure):
    doc, context, catalog, facts = fixture()
    if failure == "owner":
        facts["events"][0]["evidence_units"] = ["u0"]
    elif failure == "unknown":
        facts["events"][0]["statements"][0]["value"]["source"]["span_id"] = "v9999"
    elif failure == "catalog":
        catalog["entries"][0]["selector"]["text"] = "paraphrased"
    else:
        context["assertion_units"] = ["u0"]
    with pytest.raises(DraftError):
        compile_facts(doc, context, catalog, facts)
