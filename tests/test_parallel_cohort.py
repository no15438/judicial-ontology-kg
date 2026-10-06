"""Concurrency and immutable result retention; synthetic metadata only."""

import json
import threading

import pytest
from test_extraction import response, workspace  # noqa: F401

from judicial_kg.extraction import parallel_cohort as cohort
from judicial_kg.extraction.run import (
    CohortLease,
    busy,
    extraction_cohort,
    extraction_lock,
    run_extraction,
)
from judicial_kg.sources.records import sha256


def test_capacity_admission_keeps_default_serial_and_bounds_shared_slots():
    metrics = "llamacpp:requests_processing 7\nllamacpp:requests_deferred 0\n"
    assert busy(metrics)
    assert not busy(metrics, 8)
    assert busy(metrics.replace("processing 7", "processing 8"), 8)
    assert busy(metrics.replace("deferred 0", "deferred 1"), 8)


def test_cohort_retains_exclusive_lock_and_revokes_lease(tmp_path):
    with extraction_cohort(tmp_path, 2) as lease:
        assert lease.active
        with pytest.raises(ValueError, match="service lock"):
            with extraction_lock(tmp_path):
                pass
    assert not lease.active
    with pytest.raises(ValueError, match="lease"):
        run_extraction(tmp_path, [], None, None, cohort_lease=lease)
    with pytest.raises(ValueError, match="lease"):
        run_extraction(tmp_path, [], None, None, cohort_lease=CohortLease(tmp_path, 2))


def test_parallel_documents_overlap_and_completed_source_is_never_rerun(tmp_path, monkeypatch):
    source = tmp_path / "source.json"
    source.write_text(json.dumps([{"document_id": x} for x in "abcd"]))
    cfg = tmp_path / "config.json"
    cfg.write_text("metadata")
    old = tmp_path / "old.json"
    old.write_text(
        json.dumps(
            {
                "selected_document_id": "a",
                "completed": True,
                "frozen_inputs_unchanged": True,
                "offline_replay_verified": True,
                "status": "extraction_rejected",
            }
        )
    )
    manifest = tmp_path / "manifest.json"
    manifest.write_text(
        json.dumps(
            {
                "run": "synthetic",
                "input_hashes": {"source.json": sha256(source.read_bytes())},
                "order": ["a", "b", "c"],
                "local_config": "config.json",
                "protocol_version": 40,
                "concurrency": 2,
                "retained_runs": [
                    {"workflow_report": "old.json", "sha256": sha256(old.read_bytes())}
                ],
            }
        )
    )
    monkeypatch.setattr(
        cohort,
        "load_config",
        lambda _: {"version": "qwen-extraction-v40", "max_documents": 1, "context_tokens": 32768},
    )
    monkeypatch.setattr(cohort.subprocess, "check_output", lambda *a, **k: "synthetic\n")
    barrier = threading.Barrier(2)
    seen = []

    class Client:
        def call(self, path, body=None, timeout=15):
            if path == "/props":
                return response({"total_slots": 2, "default_generation_settings": {"n_ctx": 32768}})
            if path == "/metrics":
                from judicial_kg.extraction.transport import Response

                return Response(
                    200, b"llamacpp:requests_processing 0\nllamacpp:requests_deferred 0\n", 0
                )
            barrier.wait(timeout=5)
            return response({})

    def runner(root, paths, out, **kwargs):
        assert kwargs["cohort_lease"].active
        seen.append(kwargs["document_id"])
        kwargs["client"].call("/v1/chat/completions", {})
        return {"status": "automatic_pipeline_accepted", "automatic_pipeline_accepted": True}

    result = cohort.run_parallel(
        tmp_path, manifest, tmp_path / "artifacts/run", client=Client(), runner=runner
    )
    assert sorted(seen) == ["b", "c"]
    assert result["completed"] and result["finished_documents"] == 3
    assert result["automatic_pipeline_accepted"] == 2
    assert result["retained_completed_documents"] == 1
    assert result["new_requests_submitted"] == 2
    assert result["not_selected"] == 1
    assert result["results"][0]["status"] == "extraction_rejected"
    assert (
        sha256(old.read_bytes()) == json.loads(manifest.read_text())["retained_runs"][0]["sha256"]
    )


def test_native_parallel_receipts_replay_with_same_capacity(workspace, monkeypatch):  # noqa: F811
    from test_extraction import SimulatedClient
    from test_object_routing import test_native_runner_and_frozen_replay_keep_object_routing

    from judicial_kg.extraction import fresh_workflow
    from judicial_kg.extraction.transport import Response

    root = workspace[0]
    original_call = SimulatedClient.call
    original_run = fresh_workflow.run_extraction

    def call(self, path, body=None, timeout=15):
        if path == "/metrics":
            self.paths.append(path)
            return Response(
                200, b"llamacpp:requests_processing 1\nllamacpp:requests_deferred 0\n", 0
            )
        result = original_call(self, path, body, timeout)
        if path == "/props":
            value = result.json()
            value["total_slots"] = 2
            return response(value)
        return result

    monkeypatch.setattr(SimulatedClient, "call", call)
    with extraction_cohort(root, 2) as lease:

        def run(*args, **kwargs):
            kwargs["cohort_lease"] = lease
            return original_run(*args, **kwargs)

        monkeypatch.setattr(fresh_workflow, "run_extraction", run)
        test_native_runner_and_frozen_replay_keep_object_routing(workspace, monkeypatch, False, 40)
