"""Explicit target selection retains every source and survives zero-call staged replay."""

import json

from test_extraction import MODEL, ROOT, SimulatedClient, response, workspace  # noqa: F401

from judicial_kg.extraction.fresh_workflow import run_fresh


def prepare_selection(workspace):  # noqa: F811
    root, docs, config = workspace
    for name in (
        "ontology/private-lending-v0.3.json",
        "schemas/extraction-v0.3.schema.json",
        "prompts/qwen-extraction-v37.txt",
    ):
        (root / name).write_bytes((ROOT / name).read_bytes())
    cfg = json.loads(config.read_text())
    cfg.update(version="qwen-extraction-v37", max_documents=1)
    config.write_text(json.dumps(cfg))
    return root, docs, config


class RejectingClient(SimulatedClient):
    def call(self, path, body=None, timeout=15):
        if path != "/v1/chat/completions":
            return super().call(path, body, timeout)
        self.paths.append(path)
        return response(
            {"model": MODEL, "choices": [{"finish_reason": "stop", "message": {"content": "{}"}}]}
        )


def test_nonfirst_document_is_selected_and_failure_replays_without_losing_denominator(workspace):  # noqa: F811
    root, docs, config = prepare_selection(workspace)
    target = json.loads(docs.read_text())[2]["document_id"]
    client = RejectingClient()
    output = root / "artifacts/fresh-selected"
    report = run_fresh(
        root, [docs], output, version=37, local_config=config, document_id=target, client=client
    )
    assert report["selected_document_id"] == target
    assert report["expected_documents"] == 3 and report["not_selected"] == 2
    assert report["offline_replay_verified"] and report["frozen_inputs_unchanged"]
    assert report["status"] == "extraction_rejected"
    original = json.loads((output / "extraction/outcomes.json").read_text())
    assert [o["document_id"] for o in original if o["selected"]] == [target]
    assert len(original) == 3
    assert client.paths.count("/v1/chat/completions") == 1
    assert json.loads((output / "replay/report.json").read_text())["requests_submitted"] == 0


def test_missing_target_rejects_before_contacting_model(workspace):  # noqa: F811
    root, docs, config = prepare_selection(workspace)
    client = RejectingClient()
    report = run_fresh(
        root,
        [docs],
        root / "artifacts/missing",
        version=37,
        local_config=config,
        document_id="missing",
        client=client,
    )
    assert report["status"] == "workflow_verification_failed"
    assert client.paths == []
