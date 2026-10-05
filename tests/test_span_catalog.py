"""Source-coordinate tests on metadata only, not synthetic legal gold."""

import copy

import pytest
from jsonschema import Draft202012Validator
from test_anchored import source

from judicial_kg.extraction import span_catalog as p
from judicial_kg.sources.records import canonical_hash, verify_evidence


def catalog(proposals=()):
    return p.build_catalog(source(), ["u0", "u3"], ["A🙂"], proposals)


def selection(c):
    return {
        "catalog_sha256": canonical_hash(c),
        "mentions": [
            {"span_id": e["span_id"], "actor_id": "a", "kind": "description"} for e in c["entries"]
        ],
        "proposal_decisions": [],
    }


def test_unicode_repeats_stable_ids_and_legacy_selectors():
    c = catalog()
    assert len(c["entries"]) == 2
    assert c == p.build_catalog(source(), ["u3", "u0"], ["A🙂", "A🙂"])
    for entry in c["entries"]:
        verify_evidence(source(), entry["evidence"])
        assert entry["evidence"]["end"] - entry["evidence"]["start"] == 2
        assert entry["selector"]["occurrence"] == 0
    result = p.compile_selection(source(), ["u0", "u3"], c, ["a"], selection(c))
    assert len(result["mentions"]) == 2
    assert len({m["id"] for m in result["mentions"]}) == 2
    assert result["document_accepted"] is False
    assert result["semantic_attribution_verified"] is False


@pytest.mark.parametrize("change", ["span", "actor", "kind", "hash", "free_text", "duplicate"])
def test_serialized_generation_schema_rejects_invalid_combinations(change):
    import json

    c = catalog()
    s = selection(c)
    if change == "hash":
        s["catalog_sha256"] = "changed"
    elif change == "free_text":
        s["mentions"][0]["text"] = "different text"
    elif change == "duplicate":
        s["mentions"].append(s["mentions"][0].copy())
    else:
        s["mentions"][0][{"span": "span_id", "actor": "actor_id", "kind": "kind"}[change]] = (
            "invalid"
        )
    schema = json.loads(json.dumps(p.selection_schema(c, ["a"])))
    assert list(Draft202012Validator(schema).iter_errors(s))
    with pytest.raises(ValueError):
        p.compile_selection(source(), ["u0", "u3"], c, ["a"], s)


@pytest.mark.parametrize("change", ["scope", "source", "quote", "selector"])
def test_compile_rechecks_source_and_catalog_not_just_schema(change):
    c = catalog()
    doc = source()
    scope = ["u0", "u3"]
    if change == "scope":
        scope = ["u0"]
    elif change == "source":
        doc["fields"][0]["text"] += "X"
    elif change == "quote":
        c["entries"][0]["evidence"]["quote"] = "wrong"
    else:
        c["entries"][0]["selector"]["unit_id"] = "u9"
    with pytest.raises(ValueError):
        p.compile_selection(doc, scope, c, ["a"], selection(c))


def test_unseen_form_expands_catalog_but_requires_explicit_decision():
    c = p.build_catalog(source(), ["u1"], [], [{"id": "p1", "text": "B 42"}])
    assert c["proposal_ledger"][0]["status"] == "requires_selection"
    s = selection(c)
    with pytest.raises(ValueError):
        p.compile_selection(source(), ["u1"], c, ["a"], s)
    s["proposal_decisions"] = [
        {"proposal_id": "p1", "action": "select", "span_ids": [c["entries"][0]["span_id"]]}
    ]
    assert p.compile_selection(source(), ["u1"], c, ["a"], s)["status"] == "source_grounded_stage"


def test_zero_match_and_unsupported_cross_unit_proposals_remain_unresolved():
    c = p.build_catalog(
        source(), ["u0", "u1"], [], [{"id": "p1", "text": "MISSING"}, {"id": "p2", "text": "，B"}]
    )
    assert c["entries"] == []
    s = {
        "catalog_sha256": canonical_hash(c),
        "mentions": [],
        "proposal_decisions": [
            {"proposal_id": k, "action": "uncertain", "span_ids": []} for k in ("p1", "p2")
        ],
    }
    r = p.compile_selection(source(), ["u0", "u1"], c, ["a"], s)
    assert r["status"] == "unresolved" and len(r["unresolved_proposals"]) == 2
    assert c["complete_mention_coverage"] is False


def test_same_span_different_actor_or_kind_never_collapses():
    c = catalog()
    s = selection(c)
    m = s["mentions"][0]
    s["mentions"] = [m, {**m, "actor_id": "b"}, {**m, "kind": "name"}]
    result = p.compile_selection(source(), ["u0", "u3"], c, ["a", "b"], s)
    assert len({m["id"] for m in result["mentions"]}) == 3
    assert not result["semantic_attribution_verified"]


def test_proposal_multiple_positions_and_empty_decision_safety():
    c = catalog([{"id": "p", "text": "A🙂"}])
    s = selection(c)
    d = {"proposal_id": "p", "action": "select", "span_ids": [e["span_id"] for e in c["entries"]]}
    s["proposal_decisions"] = [d]
    assert len(p.compile_selection(source(), ["u0", "u3"], c, ["a"], s)["mentions"]) == 2
    for changed in ({**d, "span_ids": []}, {**d, "action": "uncertain"}):
        bad = copy.deepcopy(s)
        bad["proposal_decisions"] = [changed]
        with pytest.raises(ValueError):
            p.compile_selection(source(), ["u0", "u3"], c, ["a"], bad)


def test_fullwidth_spaces_and_overlapping_occurrences_are_not_normalized():
    from judicial_kg.sources.records import document

    doc = document(
        "metadata",
        {"id": 1},
        {"alpha": ("/a", "Ａ　ＡAAA。")},
        family_id="metadata:1",
        allocation="development_exposed",
    )
    c = p.build_catalog(doc, ["u0"], ["Ａ　Ａ", "AA"])
    full = [e for e in c["entries"] if e["evidence"]["quote"] == "Ａ　Ａ"]
    overlap = [e for e in c["entries"] if e["evidence"]["quote"] == "AA"]
    assert len(full) == 1 and full[0]["evidence"]["end"] == 3
    assert [e["selector"]["occurrence"] for e in overlap] == [0, 1]


def test_wrong_proposal_position_and_missing_mention_reject():
    c = catalog([{"id": "p", "text": "A🙂"}])
    s = selection(c)
    s["mentions"] = []
    s["proposal_decisions"] = [
        {"proposal_id": "p", "action": "select", "span_ids": [c["entries"][0]["span_id"]]}
    ]
    with pytest.raises(ValueError):
        p.compile_selection(source(), ["u0", "u3"], c, ["a"], s)
