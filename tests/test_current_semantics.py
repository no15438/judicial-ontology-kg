"""Lexical protocol counterexamples and metadata simulations, never a judicial gold corpus."""

import copy
import json

import pytest
from test_current import CONTRACT, ROOT, FakeClient, frame, source
from test_current_recovery import DirectCalls

from judicial_kg.current.recovery import ProtocolError
from judicial_kg.current.runner import run_document
from judicial_kg.current.semantics import (
    CONTEXT_LABELS,
    CONTEXT_NAMES,
    check_schema,
    direct_conflicts,
    repair,
    review,
    source_cues,
    speaker_options,
)
from judicial_kg.current.shared import pack, unpack
from judicial_kg.current.source import batches


def payload(text="B 42。"):
    return {
        "target_units": [{"unit": 1, "field": "metadata", "text": text}],
        "background_units": [{"unit": 0, "field": "metadata", "text": "A。"}],
    }


def supported(keys):
    keys = list(keys)
    for key in list(keys):
        if key.endswith("_context"):
            prefix = key.removesuffix("_context")
            keys.extend([prefix + "_speakers", prefix + "_scope"])
    result = {
        k: {"decision": "supported", "reason": "metadata simulation", "source_units": [1]}
        for k in keys
    }
    for k in keys:
        if k.endswith("_context"):
            result[k]["selected_kind"] = "引用材料"
        elif k.endswith("_speakers"):
            result[k]["selected_speakers"] = []
        elif not k.endswith("_scope"):
            result[k]["source_match"] = "same_context"
    return result


class ReviewCalls:
    """Schema-scoped metadata replies, never judicial sources."""

    halted = False

    def __init__(self, answer):
        self.answer = answer
        self.messages = []

    def infer(self, messages, stage):
        self.messages.append(messages)
        fmt = json.loads(messages[0]["content"].split("Schema:\n")[-1])
        return {k: copy.deepcopy(self.answer[k]) for k in fmt["properties"]}


def test_literal_source_leads_retain_prior_court_frame_without_assigning_identity():
    p = {
        "target_units": [{"unit": 3, "text": "第二、被告提供材料。"}],
        "background_units": [
            {"unit": 0, "text": "一审法院认为："},
            {"unit": 1, "text": "第一、内容。"},
            {"unit": 2, "text": "另一内容。"},
        ],
    }
    assert source_cues(p, [3]) == [p["background_units"][0]]


def test_explicit_plaintiff_introduction_cannot_be_bound_to_defendant():
    c = frame()
    c.update(kind="party_claim", speakers=[0])
    c["actors"][0]["mention"]["text"] = "被告"
    units = {1: {"text": "原告向本院提出诉讼请求："}}
    assert direct_conflicts(c, "context", c, units) == ["explicit_claimant_speaker_conflict"]
    c["actors"][0]["mention"]["text"] = "原告"
    assert not direct_conflicts(c, "context", c, units)


@pytest.mark.parametrize("text,flagged", [("转账款项并非借款，", True), ("未发生转账。", False)])
def test_negation_of_loan_nature_does_not_negate_transfer_existence(text, flagged):
    c = frame()
    e = {**c["events"][0], "event_type": "funds_transfer"}
    assert bool(direct_conflicts(c, "events", e, {1: {"text": text}})) is flagged


def statement(predicate="other", raw="42"):
    return {
        "subject_kind": "actor",
        "subject": 0,
        "predicate": predicate,
        "polarity": "affirmed",
        "units": [1],
        "value": {"kind": "text", "source": {"unit": 1, "text": raw, "occurrence": 0}},
    }


def test_interest_interval_is_not_a_due_date_but_an_explicit_due_date_is_allowed():
    c = frame()
    st = statement("repayment_deadline", "从日期甲至实际支付之日止")
    units = {1: {"text": "按年利率计算。"}}
    assert direct_conflicts(c, "statements", st, units)
    st["value"]["source"]["text"] = "日期甲"
    assert not direct_conflicts(c, "statements", st, units)


def test_cost_request_is_not_a_law_reference_but_an_explicit_provision_is_allowed():
    c = frame()
    st = statement("legal_basis", "诉讼费、保险费")
    assert direct_conflicts(c, "statements", st, {1: {"text": "费用请求。"}})
    st["value"]["source"]["text"] = "《诉讼费用交纳办法》第十条"
    assert not direct_conflicts(c, "statements", st, {1: {"text": "引用。"}})


def test_model_supported_cannot_override_a_direct_source_conflict():
    c = frame()
    c.update(kind="party_claim", speakers=[0])
    c["actors"][0]["mention"]["text"] = "被告"
    answer = supported(["c0_context", "c0_events_0"])
    answer["c0_context"]["selected_kind"] = "当事人主张"
    answer["c0_speakers"]["selected_speakers"] = ["a0:被告"]
    calls = ReviewCalls(answer)
    saved = {}
    wire = {"contexts": [c]}
    before = copy.deepcopy(wire)
    failures = review(
        wire, payload("原告向本院提出诉讼请求："), calls, "w", lambda k, v: saved.update({k: v})
    )
    assert failures[0]["key"] == "c0_context"
    assert failures[0]["direct_conflicts"] == ["explicit_claimant_speaker_conflict"]
    assert wire == before


def test_supported_semantic_review_requires_actual_source_units():
    answer = supported(["c0_context", "c0_events_0"])
    answer["c0_context"]["source_units"] = []
    failures = review({"contexts": [frame()]}, payload(), ReviewCalls(answer), "w", lambda *a: None)
    assert failures[0]["key"] == "c0_context"
    assert failures[0]["decision"]["decision"] == "uncertain"


def test_supported_review_cannot_cite_only_unrelated_background():
    answer = supported(["c0_context", "c0_events_0"])
    answer["c0_context"]["source_units"] = [0]
    failures = review({"contexts": [frame()]}, payload(), ReviewCalls(answer), "w", lambda *a: None)
    assert failures[0]["key"] == "c0_context"
    assert failures[0]["decision"]["decision"] == "uncertain"


def test_existing_unresolved_cannot_disappear_during_semantic_repair():
    c = frame()
    c["unresolved"] = [{"units": [1], "reason": "existing uncertainty"}]
    fixed = copy.deepcopy(c)
    fixed["unresolved"] = []
    answer = {
        "contexts": [fixed],
        "outcomes": {
            "events_0": {"action": "reextracted", "context": 0, "index": 0},
            "unresolved_0": {"action": "unresolved", "context": 0, "index": 0},
        },
    }
    with pytest.raises(ValueError, match="protocol_recovery_exhausted"):
        repair(
            {"contexts": [c]},
            [{"context": 0}],
            CONTRACT,
            payload(),
            DirectCalls([answer] * 3),
            "w",
            lambda *a: None,
        )


def test_semantic_repair_can_split_context_and_preserves_unaffected_contexts():
    c = frame()
    c["units"] = [0, 1]
    wire = {"contexts": [c, frame()], "coverage": []}
    one, two = copy.deepcopy(c), copy.deepcopy(c)
    one.update(units=[0], events=[], collectives=[], actors=[])
    two["units"] = [1]
    result = {
        "contexts": [one, two],
        "outcomes": {"events_0": {"action": "reextracted", "context": 1, "index": 0}},
    }
    calls = DirectCalls([result])
    got = repair(
        wire,
        [{"context": 0}],
        CONTRACT,
        {
            "target_units": payload()["target_units"] + payload()["background_units"],
            "background_units": [],
        },
        calls,
        "w",
        lambda *a: None,
    )
    assert len(got["contexts"]) == 3 and got["contexts"][-1] == wire["contexts"][-1]
    assert wire["contexts"][0]["units"] == [0, 1]


@pytest.mark.parametrize("fault", ["drop_source_unit", "drop_outcome", "change_raw_value"])
def test_repair_rejects_silent_loss_or_replacement_of_original_value(fault):
    c = frame()
    c["statements"] = [statement()]
    fixed = copy.deepcopy(c)
    outcomes = {
        "events_0": {"action": "reextracted", "context": 0, "index": 0},
        "statements_0": {"action": "reextracted", "context": 0, "index": 0},
    }
    if fault == "drop_source_unit":
        fixed["units"] = [0]
    elif fault == "drop_outcome":
        outcomes.pop("statements_0")
    else:
        fixed["statements"][0]["value"]["source"]["text"] = "43"
    answer = {"contexts": [fixed], "outcomes": outcomes}
    with pytest.raises(ValueError, match="protocol_recovery_exhausted"):
        repair(
            {"contexts": [c]},
            [{"context": 0}],
            CONTRACT,
            payload(),
            DirectCalls([answer] * 3),
            "w",
            lambda *a: None,
        )


def test_new_runner_reviews_semantics_by_default_and_replays_exactly(tmp_path):
    doc = source()
    doc["fields"] = doc["fields"][:1]
    units, _ = batches(doc)
    proposal = {
        "contexts": [frame()],
        "coverage": [
            {
                "unit": i,
                "status": "processed" if i == 1 else "no_relevant_content",
                "reason": "fixture",
            }
            for i in range(len(units))
        ],
    }
    verification = {
        f"c0_{k}_0": {"decision": "supported", "units": [1]}
        for k in ("actors", "collectives", "events")
    }
    responses = [proposal, supported(["c0_context"]), supported(["c0_events_0"]), verification]
    for name in ("first", "replay"):
        report = run_document(ROOT, doc, tmp_path / name, FakeClient(responses), run_id="metadata")
        assert report["source_semantic_review_enabled"]
        assert report["source_semantic_review_requests"] == 2
        assert report["requests"] == 4
        assert report["semantic_precision_recall_f1"] is None
    assert (tmp_path / "first/candidate.json").read_bytes() == (
        tmp_path / "replay/candidate.json"
    ).read_bytes()


def test_shared_catalogue_is_lossless_and_reduces_repeated_alignment_contexts():
    context = [{"unit": i, "text": "原文片段" * 30} for i in range(12)]
    value = {"state": {f"a{i}": {"context": context, "proposal": i} for i in range(16)}}
    encoded = pack(value)
    assert unpack(encoded) == value
    assert len(json.dumps(encoded)) < len(json.dumps(value)) / 4


def test_shared_reference_cannot_be_a_cycle_or_override_source_data():
    with pytest.raises(ValueError, match="invalid_shared_reference"):
        unpack({"shared": {"s0": {"_shared_ref": "s0"}}, "request": {"_shared_ref": "s0"}})
    with pytest.raises(ValueError, match="reserved_shared_reference"):
        pack({"state": {"_shared_ref": "from_source"}})


def test_source_selection_disagreement_cannot_hide_behind_supported_label():
    c = frame()
    c.update(kind="party_claim", speakers=[0])
    answer = supported(["c0_context", "c0_events_0"])
    answer["c0_context"]["selected_kind"] = "法院分析说理"
    answer["c0_speakers"]["selected_speakers"] = ["missing_actor"]
    failures = review({"contexts": [c]}, payload(), ReviewCalls(answer), "w", lambda *a: None)
    assert any("source_selected_context_kind_differs" in f["direct_conflicts"] for f in failures)
    assert any("source_selected_speakers_differ" in f["direct_conflicts"] for f in failures)


def test_semantic_schema_feedback_identifies_illegal_source_position():
    with pytest.raises(ProtocolError) as caught:
        check_schema(
            {"units": [0]},
            {"type": "object", "properties": {"units": {"type": "array", "items": {"enum": [1]}}}},
        )
    assert caught.value.details == [{"path": ["units", 0], "rule": "enum", "expected": [1]}]


def test_review_context_vocabulary_matches_current_contract():
    assert set(CONTEXT_LABELS) == set(CONTRACT.ontology["context_kinds"])


def test_unknown_scope_with_explicit_unresolved_does_not_force_false_speaker():
    c = frame()
    c.update(
        kind="unknown", speakers=[], events=[], unresolved=[{"units": [1], "reason": "unclear"}]
    )
    answer = supported(["c0_context"])
    answer["c0_context"]["selected_kind"] = "无法确定"
    answer["c0_speakers"]["selected_speakers"] = ["unknown"]
    assert not review({"contexts": [c]}, payload(), ReviewCalls(answer), "w", lambda *a: None)


def test_interest_start_date_does_not_become_a_due_date():
    c = frame()
    st = statement("repayment_deadline", "2012年1月1日")
    rows = {1: {"text": "按年利率6%从2012年1月1日计算至2017年5月15日"}}
    assert "interest_start_is_not_repayment_deadline" in direct_conflicts(c, "statements", st, rows)
    st["value"]["source"]["text"] = "2017年5月15日"
    assert not direct_conflicts(c, "statements", st, rows)


def test_supported_nested_source_cannot_be_promoted_into_outer_assertion():
    answer = supported(["c0_context", "c0_events_0"])
    answer["c0_events_0"]["source_match"] = "nested_source"
    failed = review({"contexts": [frame()]}, payload(), ReviewCalls(answer), "w", lambda *a: None)
    assert failed[0]["key"] == "c0_events_0"
    assert failed[0]["direct_conflicts"] == ["assertion_source_does_not_match_context"]


def test_truncated_protocol_retries_have_explicit_reason_and_distinct_attempts():
    from judicial_kg.current.recovery import infer_checked

    class Truncated:
        halted = False
        messages = []

        def infer(self, messages, stage):
            self.messages.append(messages)
            raise ValueError("response_truncated")

    calls = Truncated()
    saved = {}
    with pytest.raises(ValueError, match="protocol_recovery_exhausted"):
        infer_checked(
            calls,
            [{"role": "system", "content": "metadata"}],
            "w",
            lambda *a: None,
            lambda k, v: saved.update({k: v}),
        )
    assert calls.messages[1] != calls.messages[2]
    assert all(r["code"] == "response_truncated" for r in saved["w-recovery"])
    assert json.loads(calls.messages[2][-1]["content"])["修复轮次"] == 2


def test_frame_review_and_assertion_review_use_separate_calls():
    calls = ReviewCalls(supported(["c0_context", "c0_events_0"]))
    assert not review({"contexts": [frame()]}, payload(), calls, "w", lambda *a: None)
    assert len(calls.messages) == 2
    first = json.loads(calls.messages[0][1]["content"])["tasks"]
    second = json.loads(calls.messages[1][1]["content"])["tasks"]
    assert all(t["kind"] == "context" for t in first.values())
    assert all(t["kind"] != "context" for t in second.values())


def test_failed_source_frame_prevents_costly_or_misframed_assertion_review():
    answer = supported(["c0_context", "c0_events_0"])
    answer["c0_context"]["selected_kind"] = "法院分析说理"
    calls = ReviewCalls(answer)
    saved = {}
    assert review({"contexts": [frame()]}, payload(), calls, "w", lambda k, v: saved.update({k: v}))
    assert len(calls.messages) == 1
    assert saved["w-semantic-review"]["not_reviewed_tasks"] == ["c0_events_0"]


def test_speaker_options_expose_exact_names_without_merging_same_name_people():
    c = frame()
    c["actors"].append(copy.deepcopy(c["actors"][0]))
    assert list(speaker_options(c)) == ["a0:B", "a1:B"]
    assert set(CONTEXT_NAMES) == set(CONTEXT_LABELS)


def test_last_semantic_repair_cannot_repeat_rejected_assertion_as_supported():
    c = frame()
    answer = {
        "contexts": [c],
        "outcomes": {"events_0": {"action": "reextracted", "context": 0, "index": 0}},
    }
    with pytest.raises(ValueError, match="protocol_recovery_exhausted"):
        repair(
            {"contexts": [c]},
            [{"context": 0, "kind": "events", "key": "c0_events_0"}],
            CONTRACT,
            payload(),
            DirectCalls([answer] * 3),
            "w",
            lambda *a: None,
            abstain_failed_items=True,
        )


def test_out_of_frame_object_anchor_abstains_before_impossible_model_question():
    from judicial_kg.current.runner import verify

    c = frame()
    c["objects"] = [
        {"object_type": "instrument", "mention": {"unit": 0, "text": "A", "occurrence": 0}}
    ]
    c["events"][0]["objects"] = [0]
    calls = DirectCalls(
        [
            {
                f"c0_{k}_0": {
                    "decision": "supported" if k != "objects" else "uncertain",
                    "units": [1] if k != "objects" else [],
                }
                for k in ("actors", "collectives", "objects")
            }
        ]
    )
    saved = {}
    result = verify({"contexts": [c]}, payload(), calls, "w", lambda k, v: saved.update({k: v}))
    assert result["c0_events_0"] == {"decision": "uncertain", "units": []}
    assert saved["w-verification-audit"][0]["status"] == "source_scope_abstention"
    assert saved["w-verification-audit"][0]["required_units"] == [0]
    assert saved["w-verification-audit"][0]["allowed_units"] == [1]
    assert len(calls.stages) == 1


def test_in_frame_object_anchor_still_allows_supported_event():
    from judicial_kg.current.runner import verify

    c = frame()
    c["objects"] = [
        {"object_type": "instrument", "mention": {"unit": 1, "text": "B", "occurrence": 0}}
    ]
    c["events"][0]["objects"] = [0]
    calls = DirectCalls(
        [
            {
                f"c0_{k}_0": {"decision": "supported", "units": [1]}
                for k in ("actors", "collectives", "objects", "events")
            }
        ]
    )
    result = verify({"contexts": [c]}, payload(), calls, "w", lambda *a: None)
    assert result["c0_events_0"] == {"decision": "supported", "units": [1]}


def test_bounded_assertion_rejection_preserves_neighbors_and_original(monkeypatch):
    from test_current import compiler

    from judicial_kg.current import runner

    cc, fmt = compiler()
    wire = {
        "contexts": [frame()],
        "coverage": [{"unit": 1, "status": "processed", "reason": "metadata"}],
    }
    before = copy.deepcopy(wire)
    failure = {"context": 0, "kind": "events", "key": "c0_events_0", "reason": "nested_source"}
    monkeypatch.setattr(runner, "review_semantics", lambda *a: [failure])
    monkeypatch.setattr(runner, "repair_semantics", lambda w, *a, **kw: copy.deepcopy(w))
    calls = DirectCalls(
        [{f"c0_{k}_0": {"decision": "supported", "units": [1]} for k in ("actors", "collectives")}]
    )
    saved = {}
    doc = source()
    units, _ = batches(doc)
    out, stage = runner.compile_with_recovery(
        wire,
        cc,
        fmt,
        [1],
        payload(),
        calls,
        None,
        doc,
        units,
        [],
        "w",
        lambda k, v: saved.update({k: v}),
        semantic_review=True,
    )
    assert stage == "w-contract-repair-2"
    assert out == before == wire
    assert cc.record["actors"] and not cc.record["events"]
    assert saved[stage + "-verification-audit"][0]["status"] == "source_semantic_abstention"
    assert saved[stage + "-semantic-assertion-abstentions"]["original_wire_retained"] == before


def test_exhausted_source_frame_is_uncertain_and_cannot_fake_full_acceptance(monkeypatch):
    from test_current import compiler

    from judicial_kg.current import runner
    from judicial_kg.current.acceptance import assess

    cc, fmt = compiler()
    wire = {
        "contexts": [frame()],
        "coverage": [{"unit": 1, "status": "processed", "reason": "metadata"}],
    }
    before = copy.deepcopy(wire)
    failure = {"context": 0, "kind": "context", "key": "c0_context"}
    monkeypatch.setattr(
        runner,
        "review_semantics",
        lambda w, *a: [] if w["contexts"][0]["kind"] == "unknown" else [failure],
    )

    def broken(*a, **kw):
        raise ValueError("protocol_recovery_exhausted")

    monkeypatch.setattr(runner, "repair_semantics", broken)
    saved = {}
    doc = source()
    units, _ = batches(doc)
    out, stage = runner.compile_with_recovery(
        wire,
        cc,
        fmt,
        [1],
        payload(),
        DirectCalls([]),
        None,
        doc,
        units,
        [],
        "w",
        lambda k, v: saved.update({k: v}),
        semantic_review=True,
    )
    assert wire == before
    assert out["contexts"][0]["kind"] == "unknown"
    assert not out["contexts"][0]["events"] and not out["contexts"][0]["actors"]
    assert out["coverage"][0]["status"] == "uncertain"
    assert saved[stage + "-semantic-frame-abstentions"]["semantic_repair_success"] is False
    coverage = [{"unit": i, "status": "uncertain"} for i in range(len(units))]
    result = assess(cc.record, cc.issues, coverage, [], len(units))
    assert not result["engineering_pipeline_passed"]
    assert "empty_event_or_statement_channel" in result["blocking_reasons"]


def test_review_invalid_proof_abstains_only_invalid_answer_and_preserves_raw():
    answer = supported(["c0_context", "c0_events_0"])
    answer["c0_context"]["source_units"] = [0]
    calls = ReviewCalls(answer)
    saved = {}
    failures = review(
        {"contexts": [frame()]}, payload(), calls, "w", lambda k, v: saved.update({k: v})
    )
    assert len(calls.messages) == 3
    report = saved["w-semantic-review"]
    assert report["rule_protocol_abstention_keys"] == ["c0_context"]
    assert report["decisions"]["c0_context"]["decision"] == "uncertain"
    assert report["decisions"]["c0_speakers"]["decision"] == "supported"
    assert failures and report["not_reviewed_tasks"] == ["c0_events_0"]
    ledger = saved["w-semantic-review-000-protocol-abstentions"]
    assert ledger["original_model_answer"]["c0_context"]["source_units"] == [0]
    assert ledger["method"] == "rule" and not ledger["model_review_success"]


def test_review_budget_failure_cannot_be_hidden_as_abstention():
    class BudgetStopped:
        halted = False

        def infer(self, messages, stage):
            raise ValueError("input_budget_exceeded")

    with pytest.raises(ValueError, match="input_budget_exceeded"):
        review({"contexts": [frame()]}, payload(), BudgetStopped(), "w", lambda *a: None)


def test_contains_feedback_exposes_required_source_units():
    from judicial_kg.current.protocol import obj

    fmt = obj(source_units={"type": "array", "contains": {"enum": [1]}})
    with pytest.raises(ProtocolError) as failure:
        check_schema({"source_units": [0]}, fmt)
    assert failure.value.details[0]["expected"] == {"enum": [1]}


def test_proof_purpose_in_court_narrative_is_not_a_confirmed_repayment():
    c = frame()
    c.update(kind="court_reasoning", units=[0, 1, 2, 3, 4])
    e = {**c["events"][0], "event_type": "repayment", "polarity": "affirmed", "units": [2]}
    units = {
        i: {"text": t}
        for i, t in enumerate(
            ["原告主张，", "欲证明该项内容，", "后已偿还的事实。", "但证据不足，", "本院不予支持。"]
        )
    }
    assert direct_conflicts(c, "events", e, units) == [
        "court_assertion_overlaps_reported_proof_purpose"
    ]
    e["units"] = [4]
    assert not direct_conflicts(c, "events", e, units)
    c["kind"] = "party_claim"
    e["units"] = [2]
    assert not direct_conflicts(c, "events", e, units)


def test_proof_scope_does_not_veto_an_explicit_court_finding_without_purpose_cue():
    c = frame()
    c["kind"] = "court_finding"
    assert not direct_conflicts(c, "events", c["events"][0], {1: {"text": "本院确认该项内容。"}})


def test_repaired_context_cannot_drop_the_original_proof_purpose_qualifier():
    c = frame()
    c.update(kind="court_reasoning", units=[2])
    e = {**c["events"][0], "event_type": "repayment", "polarity": "affirmed", "units": [2]}
    units = {
        0: {"text": "原告主张，"},
        1: {"text": "欲证明该项内容，"},
        2: {"text": "后已偿还的事实。"},
        3: {"text": "但证据不足。"},
    }
    assert direct_conflicts(c, "events", e, units) == [
        "court_assertion_overlaps_reported_proof_purpose"
    ]
