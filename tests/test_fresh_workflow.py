"""Fresh orchestration gates on synthetic artifacts, not extraction quality."""

import json

import pytest

from judicial_kg.extraction import fresh_workflow as workflow


@pytest.mark.parametrize(
    "mode", ["success", "correction", "rejected", "reused", "changed", "replay_bad", "graph_bad"]
)
def test_fresh_controller_never_accepts_external_reuse_or_mutation(tmp_path, monkeypatch, mode):
    root = tmp_path
    (root / "artifacts").mkdir()
    (root / "src").mkdir()
    code = root / "src/example.py"
    code.write_text("fixed")
    docs = root / "artifacts/documents.json"
    docs.write_text("[]")
    monkeypatch.setattr(workflow.subprocess, "check_output", lambda *a, **k: "synthetic-commit\n")
    calls = []

    def extract(root, paths, output, **kwargs):
        calls.append("extract")
        assert "resume_from" not in kwargs and "restart_stage" not in kwargs
        assert kwargs["previous_runs"] == []
        output.mkdir()
        (output / "accepted.json").write_text(json.dumps([{"synthetic": True}]))
        if mode == "changed":
            code.write_text("changed")
        return {
            "status": "rejected" if mode == "rejected" else "accepted_candidate",
            "expected_documents": 36,
            "selected_documents": 1,
            "not_selected": 35,
            "requests_submitted": 2,
            "responses_reused": int(mode == "reused"),
            "model_correction_calls": int(mode == "correction"),
            "observed_cost_cny": "0.01",
            "first_pass_accepted": mode != "correction",
        }

    def replay(*args):
        calls.append("replay")
        if mode == "replay_bad":
            raise ValueError("Replay differs")
        return {"outcome_and_artifact_equality_verified": True, "requests_submitted": 0}

    def assembly(*args, **kwargs):
        calls.append("assembly")
        return {
            "documents_assembled": 0 if mode == "graph_bad" else 1,
            "documents_assembly_failed": int(mode == "graph_bad"),
            "nodes": 1,
            "edges": 0,
            "validation": {},
            "diagnostic_hint_counts": {},
            "assembled_documents_diagnostics": {},
            "graph_canonical_sha256": "synthetic",
        }

    monkeypatch.setattr(workflow, "run_cloud", extract)
    monkeypatch.setattr(workflow, "replay_cloud", replay)
    monkeypatch.setattr(workflow, "run_assembly", assembly)
    result = workflow.run_fresh(root, [docs], root / "artifacts/fresh", previous_runs=[])
    assert result["automatic_pipeline_accepted"] == (mode in ("success", "correction"))
    assert result["uncorrected_first_pass_accepted"] == (mode == "success")
    if mode in ("changed", "reused"):
        assert calls == ["extract"]
    if mode in ("rejected", "replay_bad"):
        assert calls == ["extract", "replay"]
    assert result["not_selected"] == 35
    assert json.loads((root / "artifacts/fresh/workflow-report.json").read_text()) == result


@pytest.mark.parametrize("mode", ["success", "correction", "rejected", "changed", "replay_bad"])
def test_local_fresh_uses_native_model_receipts_and_fails_closed(tmp_path, monkeypatch, mode):
    root = tmp_path
    (root / "artifacts").mkdir()
    docs = root / "artifacts/documents.json"
    docs.write_text("[]")
    config = root / "artifacts/config.json"
    config.write_text("fixed-config")
    monkeypatch.setattr(
        workflow,
        "load_config",
        lambda p: {
            "version": "qwen-extraction-v32",
            "model": "target-next-flash",
            "max_documents": 1,
        },
    )
    monkeypatch.setattr(workflow.subprocess, "check_output", lambda *a, **k: "test-commit")
    monkeypatch.setattr(
        workflow, "run_cloud", lambda *a, **k: pytest.fail("Cloud not authorized here")
    )

    def native(root, paths, config_path, output, **kwargs):
        assert config_path == config
        output.mkdir()
        (output / "accepted.json").write_text('[{"synthetic":true}]')
        (output / "outcomes.json").write_text(
            json.dumps(
                [
                    {
                        "selected": True,
                        "status": "rejected" if mode == "rejected" else "accepted_candidate",
                        "stages": [
                            {
                                "stage": "forms-repair-1" if mode == "correction" else "forms",
                                "completion_rpc": 1,
                            }
                        ],
                    }
                ]
            )
        )
        if mode == "changed":
            config.write_text("mutated")
        return {"expected_documents": 36, "selected_documents": 1, "requests_submitted": 1}

    def replay(*args):
        if mode == "replay_bad":
            raise RuntimeError("Request mismatch")
        return {"requests_submitted": 0, "outcome_and_candidate_equality_verified": True}

    def graph(*args, **kwargs):
        return {
            "documents_assembled": 1,
            "documents_assembly_failed": 0,
            "nodes": 1,
            "edges": 0,
            "validation": {},
            "diagnostic_hint_counts": {},
            "assembled_documents_diagnostics": {},
            "graph_canonical_sha256": "test",
        }

    monkeypatch.setattr(workflow, "run_extraction", native)
    monkeypatch.setattr(workflow, "replay_completed", replay)
    monkeypatch.setattr(workflow, "run_assembly", graph)
    result = workflow.run_fresh(
        root, [docs], root / "artifacts/fresh", version=32, local_config=config
    )
    assert result["model"] == "target-next-flash"
    assert result["cloud_requests_submitted"] == 0
    assert result["automatic_pipeline_accepted"] == (mode in ("success", "correction"))
    assert result["uncorrected_first_pass_accepted"] == (mode == "success")
    assert result["observed_cost_cny"] == "0"
