"""Batch state and isolation tests; metadata fixtures are not case gold."""

import json

import pytest

from judicial_kg.extraction import batch_workflow as batch
from judicial_kg.sources.records import sha256


def fixture(tmp_path, monkeypatch):
    docs = tmp_path / "documents.json"
    docs.write_text(json.dumps([{"document_id": "a"}, {"document_id": "b"}, {"document_id": "c"}]))
    config = tmp_path / "config.json"
    config.write_text("registered")
    plan = tmp_path / "plan.json"
    plan.write_text(
        json.dumps(
            {
                "input_hashes": {"documents.json": sha256(docs.read_bytes())},
                "order": ["a", "b"],
                "local_config": "config.json",
                "protocol_version": 40,
                "run": "metadata",
            }
        )
    )
    monkeypatch.setattr(
        batch,
        "load_config",
        lambda p: {
            "version": "qwen-extraction-v40",
            "max_documents": 1,
            "model": "metadata",
            "ssh_host": "spark-lan",
            "port": 8003,
        },
    )
    monkeypatch.setattr(batch.subprocess, "check_output", lambda *a, **k: "fixed\n")
    return plan, tmp_path / "artifacts/batch"


def test_rejection_kept_and_next_document_runs_without_reuse(tmp_path, monkeypatch):
    plan, out = fixture(tmp_path, monkeypatch)
    seen = []

    class Client:
        def call(self, *args):
            return "response"

    def run(root, paths, destination, **kwargs):
        seen.append(kwargs["document_id"])
        assert kwargs["local_config"] == tmp_path / "config.json"
        assert "previous_runs" not in kwargs
        kwargs["client"].call("/v1/chat/completions", {})
        return {
            "status": "extraction_rejected" if len(seen) == 1 else "automatic_pipeline_accepted",
            "automatic_pipeline_accepted": len(seen) == 2,
        }

    result = batch.run_batch(
        tmp_path, plan, out, client=Client(), runner=run, readiness=lambda: None
    )
    assert seen == ["a", "b"]
    assert result["completed"] and result["requests_submitted"] == 2
    assert result["automatic_pipeline_accepted"] == 1
    assert result["expected_documents"] == 3 and result["not_selected"] == 1
    assert result["results"][0]["status"] == "extraction_rejected"
    assert json.loads((out / "batch-report.json").read_text()) == result
    with pytest.raises(FileExistsError):
        batch.run_batch(tmp_path, plan, out, client=Client(), runner=run, readiness=lambda: None)


@pytest.mark.parametrize("mutation", ["source", "manifest"])
def test_mid_batch_change_blocks_remaining_documents(tmp_path, monkeypatch, mutation):
    plan, out = fixture(tmp_path, monkeypatch)
    seen = []

    def run(*args, **kwargs):
        seen.append(kwargs["document_id"])
        target = tmp_path / "documents.json" if mutation == "source" else plan
        target.write_text(target.read_text() + " ")
        return {"status": "extraction_rejected"}

    result = batch.run_batch(
        tmp_path, plan, out, client=object(), runner=run, readiness=lambda: None
    )
    assert seen == ["a"]
    assert result["status"] == "blocked" and not result["completed"]
    assert result["results"][1]["status"] == "queued"


@pytest.mark.parametrize("order", [["a", "a"], ["missing"], []])
def test_invalid_selection_never_starts(tmp_path, monkeypatch, order):
    plan, out = fixture(tmp_path, monkeypatch)
    value = json.loads(plan.read_text())
    value["order"] = order
    plan.write_text(json.dumps(value))
    with pytest.raises(ValueError):
        batch.run_batch(tmp_path, plan, out)
    assert not out.exists()
