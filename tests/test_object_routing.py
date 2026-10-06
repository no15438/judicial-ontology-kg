"""Metadata-only fixtures: object routing is not a legal semantic gold test."""

import copy
import json

import pytest
from test_anchored import PROVENANCE, source
from test_extraction import MODEL, SimulatedClient, response, workspace  # noqa: F401
from test_scoped import ROOT, record

from judicial_kg.contracts import Contract, candidate_record
from judicial_kg.extraction import object_routing
from judicial_kg.extraction.draft import DraftError
from judicial_kg.graph.assembly import restore_records
from judicial_kg.graph.run import assemble_batch
from judicial_kg.sources.records import evidence_span
from judicial_kg.validation import validate_record

CONTRACT = Contract.load(ROOT, "0.4.0")


def router(answers):
    calls, saved = [], {}

    def infer(messages, fmt, stage):
        calls.append((messages, fmt, stage))
        return json.dumps(answers.pop(0))

    return object_routing.ObjectRouter(source(), infer, saved.__setitem__), calls, saved


def with_object():
    r, _, _ = router([{"p0": "project"}])
    assert r.route({"forms": ["42"]}, ["u1"], "body-000") == {"forms": []}
    result = r.compile(record())
    result["events"] = [
        {
            "id": "e",
            "event_type": "other",
            "context_id": "quote",
            "polarity": "negated",
            "participants": [{"actor_id": "b", "role": "other"}],
            "evidence": result["contexts"][1]["assertion_evidence"],
        }
    ]
    return r, result


def test_router_retains_objects_and_exact_positions_without_actor_conversion():
    r, calls, saved = router([{"p0": "actor_candidate", "p1": "platform"}])
    assert r.route({"forms": ["B", "42"]}, ["u1"], "inventory") == {"forms": ["B"]}
    ledger = saved["inventory-object-routing-ledger"]
    assert ledger["input"]["forms"] == ["B", "42"]
    assert ledger["semantic_verified"] is False
    assert ledger["decisions"][1]["positions"] == [evidence_span(source(), "alpha", 5, 7)]
    payload = json.loads(calls[0][0][1]["content"])
    assert set(payload) == {"proposals", "source_units", "adjacent_units"}
    assert payload["proposals"][1]["positions"] == [{"unit_id": "u1", "start": 5, "end": 7}]
    assert "field_sha256" not in json.dumps(payload)
    result = r.compile(record())
    assert result["actors"] == record()["actors"]
    assert result["objects"][0]["object_type"] == "platform"
    assert result["object_mentions"][0]["context_id"] == "quote"
    assert not validate_record(result, source(), CONTRACT)


@pytest.mark.parametrize(
    "answer", [{}, {"p0": "unknown"}, {"p0": "project", "p1": "project"}, {"p0": "uncertain"}]
)
def test_invalid_or_unresolved_classification_never_becomes_empty_success(answer):
    r, _, _ = router([answer])
    with pytest.raises(DraftError):
        r.route({"forms": ["42"]}, ["u1"], "inventory")


def test_missing_source_is_rejected_before_model_call():
    r, calls, _ = router([])
    with pytest.raises(DraftError, match="object_routing_missing_source"):
        r.route({"forms": ["missing"]}, ["u1"], "inventory")
    assert calls == []


def test_repeated_discovery_reuses_only_same_source_position_and_rejects_type_conflict():
    r, _, _ = router([{"p0": "project"}, {"p0": "project"}, {"p0": "platform"}])
    r.route({"forms": ["42"]}, ["u1"], "inventory")
    r.route({"forms": ["42"]}, ["u1"], "body-000")
    result = r.compile(record())
    assert len(result["objects"]) == len(result["object_mentions"]) == 1
    r.route({"forms": ["42"]}, ["u1"], "body-001")
    with pytest.raises(DraftError, match="object_type_conflicting_overlap"):
        r.compile(record())


def test_actor_object_conflict_and_uncovered_scope_fail_without_erasure():
    r, _, _ = router([{"p0": "project"}])
    r.route({"forms": ["B"]}, ["u1"], "inventory")
    with pytest.raises(DraftError, match="same_position_actor_object_conflict"):
        r.compile(record())
    r, _, _ = router([{"p0": "project"}])
    r.route({"forms": ["C"]}, ["u2"], "inventory")
    with pytest.raises(DraftError, match="object_mention_scope_not_unique"):
        r.compile(record())


def linked_record():
    r, result = with_object()
    mid = result["object_mentions"][0]["id"]
    r.infer = lambda *_: json.dumps({mid: {"decision": "linked", "event_ids": ["e"]}})
    return r.link_events(result)


def test_linked_object_roundtrips_without_becoming_event_actor():
    result = linked_record()
    assert not validate_record(result, source(), CONTRACT)
    assert result["object_links"][0]["polarity"] == "negated"
    assert result["events"][0]["participants"] == [{"actor_id": "b", "role": "other"}]
    batch = assemble_batch([source()], [json.dumps(result)], CONTRACT)
    assert batch["summary"]["documents_assembled"] == 1
    assert restore_records(batch["graph"]) == [result]
    assert batch["summary"]["semantic_precision_recall_f1"] is None
    assert {n["kind"] for n in batch["graph"]["nodes"]} >= {"Object", "ObjectMention", "ObjectLink"}


@pytest.mark.parametrize(
    "decision,ids,error",
    [
        ("no_supported_event", [], None),
        ("uncertain", [], "object_event_link_unresolved"),
        ("linked", [], "object_event_link_inconsistent"),
        ("no_supported_event", ["e"], "object_event_link_inconsistent"),
    ],
)
def test_link_decisions_do_not_guess_or_discard_objects(decision, ids, error):
    r, result = with_object()
    original = copy.deepcopy(result)
    mid = result["object_mentions"][0]["id"]
    r.infer = lambda *_: json.dumps({mid: {"decision": decision, "event_ids": ids}})
    if error:
        with pytest.raises(DraftError, match=error):
            r.link_events(result)
    else:
        assert r.link_events(result) == result
    assert result == original


def test_no_eligible_event_keeps_unlinked_object_without_model_call():
    r, result = with_object()
    result["events"] = []

    def forbidden(*_):
        raise AssertionError("No eligible events")

    r.infer = forbidden
    assert r.link_events(result) == result


@pytest.mark.parametrize(
    "change,code",
    [
        (
            lambda r: r["object_mentions"][0].update(context_id="c"),
            "object_mention_outside_assertion_scope",
        ),
        (
            lambda r: r["object_links"][0].update(polarity="affirmed"),
            "object_event_polarity_mismatch",
        ),
        (lambda r: r["object_links"][0].update(context_id="c"), "object_event_context_mismatch"),
        (
            lambda r: r["object_links"][0].update(
                evidence=[evidence_span(source(), "alpha", 3, 4)]
            ),
            "object_link_outside_event_evidence",
        ),
        (
            lambda r: r["object_mentions"][0]["evidence"].update(quote="changed"),
            "evidence_mismatch",
        ),
        (lambda r: r["objects"][0].update(object_type="organization"), "schema"),
    ],
)
def test_contract_rejects_scope_type_and_evidence_corruption(change, code):
    result = linked_record()
    change(result)
    issues = validate_record(result, source(), CONTRACT)
    assert any(code in i["code"] for i in issues), issues


def test_candidate_envelope_includes_required_object_collections():
    result = candidate_record(source(), PROVENANCE, ["alpha", "beta"], "0.4.0")
    assert result["objects"] == result["object_mentions"] == result["object_links"] == []
    assert not validate_record(result, source(), CONTRACT)


def test_extract_hooks_compile_and_validate_whole_record(monkeypatch):
    def fake_core(doc, prompt, contract, provenance, infer, save, **kwargs):
        assert contract.ontology["version"] == "0.3.0"
        assert kwargs["classifier"](doc, {"forms": ["42"]}, infer, save) == {"forms": []}
        assert kwargs["form_router"]({"forms": ["42"]}, ["u1"], "body-000") == {"forms": []}
        return record()

    monkeypatch.setattr(object_routing.referent_inventory, "extract", fake_core)
    r, calls, saved = router([{"p0": "project"}, {"p0": "project"}])
    result = object_routing.extract(
        source(), "metadata", CONTRACT, PROVENANCE, r.infer, saved.__setitem__
    )
    assert len(calls) == 2
    assert len(result["objects"]) == 1
    assert result == saved["object-record-complete"]


@pytest.mark.parametrize("unresolved", [False, True])
def test_native_runner_and_frozen_replay_keep_object_routing(workspace, monkeypatch, unresolved):  # noqa: F811
    from judicial_kg.extraction.fresh_workflow import run_fresh

    root, docs, config = workspace
    for name in (
        "ontology/private-lending-v0.4.json",
        "schemas/extraction-v0.4.schema.json",
        object_routing.PROMPT_FILE,
    ):
        (root / name).write_bytes((ROOT / name).read_bytes())
    docs.write_text(json.dumps([source()]))
    config.write_bytes((ROOT / "configs/qwen-extraction-v38-pilot.json").read_bytes())

    def fake_core(doc, prompt, contract, provenance, infer, save, **kwargs):
        kwargs["classifier"](doc, {"forms": ["42"]}, infer, save)
        value = record()
        value["provenance"] = provenance
        return value

    monkeypatch.setattr(object_routing.referent_inventory, "extract", fake_core)

    class Client(SimulatedClient):
        def call(self, path, body=None, timeout=15):
            if path != "/v1/chat/completions":
                return super().call(path, body, timeout)
            self.paths.append(path)
            return response(
                {
                    "model": MODEL,
                    "choices": [
                        {
                            "finish_reason": "stop",
                            "message": {
                                "content": json.dumps(
                                    {"p0": "uncertain" if unresolved else "project"}
                                )
                            },
                        }
                    ],
                }
            )

    client = Client()
    result = run_fresh(
        root,
        [docs],
        root / "artifacts/fresh",
        version=38,
        local_config=config,
        client=client,
        document_id=source()["document_id"],
    )
    assert result["requests_submitted"] == 1
    assert client.paths.count("/v1/chat/completions") == 1
    assert result["offline_replay_verified"]
    assert result["frozen_inputs_unchanged"]
    assert result["automatic_pipeline_accepted"] is not unresolved
    if not unresolved:
        assert result["graph"]["nodes"] > 0
        assert result["uncorrected_first_pass_accepted"]
