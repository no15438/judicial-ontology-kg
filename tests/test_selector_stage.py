"""Metadata-only staging checks, never legal gold."""

import copy

import pytest
from test_extraction import workspace  # noqa: F401
from test_selector_repair import decision, example

from judicial_kg.extraction.anchored import GroundingError
from judicial_kg.extraction.draft import DraftError
from judicial_kg.extraction.selector_repair import apply_choices, stage_choices


def test_resolved_positions_survive_pending_decisions_without_acceptance():
    _, draft, plan, choices = example()
    original = copy.deepcopy(draft)
    choices["selector_choices"][1] = decision(plan["selector_items"][1], "uncertain")
    patched, counts, pending = stage_choices(draft, plan, choices)
    assert draft == original
    assert patched["mentions"][0]["evidence"]["occurrence"] == 0
    assert patched["mentions"][1] == original["mentions"][1]
    assert counts == {"locate": 1, "uncertain": 1}
    assert len(pending) == 1
    assert patched["contexts"] == original["contexts"]
    assert patched["actors"] == original["actors"]
    with pytest.raises(GroundingError):
        apply_choices(draft, plan, choices)


@pytest.mark.parametrize("bad", ["choice_id", "local_text", "mention_kind"])
def test_uncertainty_is_not_a_patch_or_a_deletion(bad):
    _, draft, plan, choices = example()
    choices["selector_choices"][1] = decision(plan["selector_items"][1], "uncertain", **{bad: "x"})
    with pytest.raises(DraftError):
        stage_choices(draft, plan, choices)


def test_all_resolved_staging_matches_existing_application():
    _, draft, plan, choices = example()
    patched, counts, pending = stage_choices(draft, plan, choices)
    assert pending == []
    assert (patched, counts) == apply_choices(draft, plan, choices)


def test_mixed_invalid_and_pending_documents_do_not_abort_or_convert_decisions(workspace):  # noqa: F811
    import json
    from pathlib import Path

    from test_extraction import MODEL, SimulatedClient, response
    from test_selector_repair import example

    from judicial_kg.extraction.run import run_extraction
    from judicial_kg.extraction.selector_repair import LOCAL_SPAN_VERSION, run_repair
    from judicial_kg.extraction.selector_stage import prepare_stages
    from judicial_kg.sources.records import document

    root, docs, config = workspace
    project = Path(__file__).resolve().parents[1]
    for name in [
        "ontology/private-lending-v0.2.json",
        "schemas/extraction-v0.2.schema.json",
        "prompts/qwen-extraction-v6.txt",
        "prompts/qwen-selector-repair-v3.txt",
    ]:
        (root / name).write_bytes((project / name).read_bytes())
    doc, draft, plan, ds = example()
    second = document(
        "metadata",
        {"record_pointer": "/1"},
        {"field": ("/1/f", doc["fields"][0]["text"])},
        family_id="family:second",
        allocation="development_exposed",
    )
    docs.write_text(json.dumps([doc, second]))
    cfg = json.loads(config.read_text())
    cfg.update(version="qwen-extraction-v6", max_documents=2)
    config.write_text(json.dumps(cfg))

    class Client(SimulatedClient):
        def __init__(self):
            super().__init__()
            self.index = 0

        def call(self, path, body=None, timeout=15):
            if path != "/v1/chat/completions":
                return super().call(path, body, timeout)
            value = copy.deepcopy(draft)
            if "selector_items" in body["messages"][-1]["content"]:
                value = copy.deepcopy(ds)
                if self.index == 0:
                    value["selector_choices"][0] = decision(
                        plan["selector_items"][0],
                        "local_span",
                        local_text="absent",
                        mention_kind="name",
                    )
                else:
                    value["selector_choices"][1] = decision(plan["selector_items"][1], "uncertain")
                self.index += 1
            return response(
                {
                    "model": MODEL,
                    "choices": [
                        {"finish_reason": "stop", "message": {"content": json.dumps(value)}}
                    ],
                }
            )

    run_extraction(root, [docs], config, root / "artifacts/original", Client())
    spec = root / "spec.json"
    spec.write_text(
        json.dumps(
            [
                {
                    "source_run": "artifacts/original",
                    "documents": [docs.name],
                    "document_ids": [doc["document_id"], second["document_id"]],
                }
            ]
        )
    )
    cfg["version"] = LOCAL_SPAN_VERSION
    config.write_text(json.dumps(cfg))
    saved = root / "artifacts/selectors"
    run_repair(root, [docs], spec, config, saved, Client())
    result = prepare_stages(root, [docs], spec, saved, root / "artifacts/stages")
    stages = json.loads((root / "artifacts/stages/stages.json").read_bytes())
    bad = stages[doc["document_id"]]
    good = stages[second["document_id"]]
    assert bad["status"] == "invalid_parent_decisions" and bad["draft"] == draft
    assert bad["decision_counts"] == {} and bad["pending_decisions"] == []
    assert bad["validation_issues"][0]["code"] == "selector_repair_local_span_invalid"
    assert bad["decisions"]["selector_choices"][0]["action"] == "local_span"
    assert good["status"] == "unresolved" and good["decision_counts"] == {
        "locate": 1,
        "uncertain": 1,
    }
    assert result["document_status_counts"] == {"invalid_parent_decisions": 1, "unresolved": 1}
    assert result["selected_documents"] == 2 and result["resolved_decisions"] == 1
