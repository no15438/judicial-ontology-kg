"""Discovery request tests use metadata only, not invented case gold."""

import copy

import pytest

from judicial_kg.extraction import source_discovery


def test_discovery_replaces_conflict_without_mutating_source_or_original():
    messages = [
        {"role": "system", "content": "只从给定原文提取真实出现的人物/机构名称。也提取项目。"},
        {"role": "user", "content": '{"source_units": [{"text": " A　B "}]}'},
    ]
    original = copy.deepcopy(messages)
    result = source_discovery.discovery_messages(messages, "body-006-forms")
    assert messages == original
    assert result[1:] == original[1:]
    assert "只从给定原文提取真实出现的人物/机构" not in result[0]["content"]
    assert "项目和平台不必是人物或机构" in result[0]["content"]
    assert "集合成员不明确仍保留" in result[0]["content"]


@pytest.mark.parametrize(
    "stage", ["inventory-forms", "anonymous-forms", "body-006-mentions", "body-006-forms-repair-1"]
)
def test_other_stages_keep_their_own_contract(stage):
    messages = [{"role": "system", "content": "own instruction"}]
    assert source_discovery.discovery_messages(messages, stage) is messages


def test_extract_applies_discovery_at_transport_boundary(monkeypatch):
    messages = [
        {"role": "system", "content": "legacy restriction"},
        {"role": "user", "content": "source"},
    ]
    fmt = {"schema": {"required": ["forms"]}}
    calls = []

    def core(doc, prompt, contract, provenance, infer, save):
        return infer(messages, fmt, "body-006-forms")

    def infer(received, received_fmt, stage):
        calls.append((received, received_fmt, stage))
        return "wire"

    monkeypatch.setattr(source_discovery.position_consistency, "extract", core)
    assert source_discovery.extract({}, "", None, {}, infer, None) == "wire"
    assert calls[0][0][0]["content"] == source_discovery.DISCOVERY_INSTRUCTION
    assert calls[0][1] is fmt
    assert calls[0][0][1] == messages[1]
