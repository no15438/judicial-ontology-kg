import json
import threading
from concurrent.futures import ThreadPoolExecutor
from decimal import Decimal

import pytest

from judicial_kg.extraction.cloud_cohort import SharedBudget
from judicial_kg.extraction.transport import Response


def test_shared_budget_reserves_before_concurrent_call(tmp_path):
    entered, release = threading.Event(), threading.Event()

    class Client:
        calls = 0

        def call(self, body, timeout):
            self.calls += 1
            entered.set()
            assert release.wait(5)
            return Response(
                200, json.dumps({"usage": {"prompt_tokens": 1, "completion_tokens": 1}}).encode(), 1
            )

    client = Client()
    guard = SharedBudget(client, "199.9", "200", tmp_path / "budget.json")
    body = {"model": "qwen3.8-max-0902", "messages": [{"content": "x"}], "max_tokens": 1000}
    with ThreadPoolExecutor(2) as pool:
        first = pool.submit(guard.call, body, 10)
        assert entered.wait(5)
        second = guard.call(body, 10)
        assert second.error == "shared_budget_exhausted"
        release.set()
        assert first.result().status == 200
    assert client.calls == 1
    assert guard.committed == Decimal("0.000048")


def test_unknown_usage_keeps_reservation(tmp_path):
    class Client:
        def call(self, body, timeout):
            return Response(None, b"", 1, "timeout")

    guard = SharedBudget(Client(), "199.9", "200", tmp_path / "budget.json")
    body = {"model": "qwen3.8-max-0902", "messages": [{"content": "x"}], "max_tokens": 1000}
    guard.call(body, 10)
    assert guard.committed == Decimal("0.085164")
    assert guard.call(body, 10).error == "shared_budget_exhausted"


@pytest.mark.parametrize("model", ["qwen3.8-max-0902", "qwen3.8-flash"])
@pytest.mark.parametrize("version", [40, 41, 42])
def test_v40_cloud_failure_replays_under_cohort_lease(tmp_path, version, model):
    import shutil
    from pathlib import Path

    from judicial_kg.extraction.bailian_staged import replay_cloud, run_cloud
    from judicial_kg.extraction.run import extraction_cohort

    root = Path(__file__).resolve().parents[1]
    for folder in ("ontology", "schemas", "prompts"):
        shutil.copytree(root / folder, tmp_path / folder)
    # Reuse the repository's explicitly synthetic source fixture.
    from test_extraction import source as source_fixture

    doc = source_fixture()
    source = tmp_path / "artifacts/source.json"
    source.parent.mkdir()
    source.write_text(json.dumps([doc]))

    class Client:
        def call(self, body, timeout):
            return Response(503, b"{}", 0.1)

    with extraction_cohort(tmp_path, 4) as lease:
        import unittest.mock

        with unittest.mock.patch(
            "judicial_kg.extraction.bailian_staged.subprocess.check_output", return_value="test"
        ):
            result = run_cloud(
                tmp_path,
                [source],
                tmp_path / "artifacts/run",
                version=version,
                model=model,
                client=Client(),
                cohort_lease=lease,
            )
            replay = replay_cloud(
                tmp_path,
                [source],
                tmp_path / "artifacts/run",
                tmp_path / "artifacts/replay",
                cohort_lease=lease,
            )
    assert result["status"] == "transport_or_http_failed"
    assert replay["outcome_and_artifact_equality_verified"]


def test_flash_rates_and_usage_settlement(tmp_path):
    from judicial_kg.extraction.bailian_staged import cost

    assert cost(1000000, 1000000, "qwen3.8-flash") == Decimal("3.5")

    class Client:
        def call(self, body, timeout):
            return Response(
                200,
                json.dumps({"usage": {"prompt_tokens": 100, "completion_tokens": 10}}).encode(),
                0,
            )

    guard = SharedBudget(
        Client(), "71.125260", "76.125260", tmp_path / "budget.json", "qwen3.8-flash"
    )
    guard.call({"model": "qwen3.8-flash", "messages": [{"content": "x"}], "max_tokens": 100}, 10)
    assert guard.committed == Decimal("0.000107")
    assert guard.calls == 1
