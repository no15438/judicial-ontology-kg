import json

import pytest

from judicial_kg.extraction.draft import DraftError
from judicial_kg.extraction.structured_response import ResponseGuard

SCHEMA = {"type": "object", "properties": {"forms": {"type": "array"}}}
MESSAGES = [{"role": "system", "content": "source data"}, {"role": "user", "content": "{}"}]


def test_echo_is_corrected_once_and_original_preserved():
    saved, calls = {}, []

    def infer(messages, fmt, stage):
        calls.append(stage)
        return json.dumps(SCHEMA if len(calls) == 1 else {"forms": []})

    guard = ResponseGuard(infer, saved.__setitem__)
    assert json.loads(guard.call(MESSAGES, {"schema": SCHEMA}, "anonymous-forms")) == {"forms": []}
    assert calls == ["anonymous-forms", "anonymous-forms-repair-schema-1"]
    assert saved["anonymous-forms-schema-echo-original"] == SCHEMA
    assert MESSAGES[0]["content"] == "source data"


def test_persistent_echo_stops_without_loop():
    calls = []

    def infer(*args):
        calls.append(1)
        return json.dumps(SCHEMA)

    guard = ResponseGuard(infer, lambda *a: None)
    with pytest.raises(DraftError, match="schema_echo_unresolved"):
        guard.call(MESSAGES, {"schema": SCHEMA}, "anonymous-forms")
    assert len(calls) == 2


def test_empty_binding_audited_never_repaired_or_deleted():
    raw = {"mentions": {"s1": {}}, "proposal_decisions": {"p1": "uncertain"}}
    payload = {"catalog": {"entries": [{"span_id": "s1", "text": "A"}]}, "source_units": []}
    saved = {}
    guard = ResponseGuard(lambda *a: json.dumps(raw), saved.__setitem__)
    messages = [MESSAGES[0], {"role": "user", "content": json.dumps(payload)}]
    with pytest.raises(DraftError, match="mention_binding_unresolved"):
        guard.call(messages, {"schema": {}}, "body-001-mentions")
    audit = saved["body-001-mentions-unresolved-bindings"]
    assert audit["original_response"] == raw
    assert audit["source_positions"][0]["span_id"] == "s1"
    assert guard.repairs == 0


def test_modified_forms_schema_echo_is_recognized():
    from judicial_kg.extraction.structured_response import is_schema_echo

    changed = {"type": "object", "properties": {"forms": {"type": "array", "maxItems": 50}}}
    assert is_schema_echo(changed, SCHEMA)
    assert not is_schema_echo({"forms": []}, SCHEMA)


def test_document_repair_budget_blocks_third_echo():
    count = 0

    def infer(messages, fmt, stage):
        nonlocal count
        count += 1
        return json.dumps({"forms": []} if "-repair-" in stage else SCHEMA)

    guard = ResponseGuard(infer, lambda *a: None)
    for stage in ["one", "two"]:
        guard.call(MESSAGES, {"schema": SCHEMA}, stage)
    with pytest.raises(DraftError, match="schema_echo_repair_budget_exhausted"):
        guard.call(MESSAGES, {"schema": SCHEMA}, "three")
    assert count == 5
