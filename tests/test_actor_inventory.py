"""Frozen inventory lineage and missing-identity rejection on metadata, not legal gold."""

import copy
import json

import pytest
from test_anchored import PROVENANCE, source
from test_context_first import prepare
from test_extraction import MODEL, SimulatedClient, response, workspace  # noqa: F401
from test_scoped import CONTRACT, ROOT, fixtures

from judicial_kg.extraction import actor_inventory as protocol
from judicial_kg.extraction import span_catalog
from judicial_kg.extraction.anchored import source_units
from judicial_kg.extraction.draft import DraftError
from judicial_kg.extraction.replay import replay_completed
from judicial_kg.extraction.run import run_extraction
from judicial_kg.sources.records import canonical_hash


def selection(catalog):
    span = catalog["proposal_ledger"][0]["span_ids"][0]
    return {
        "catalog_sha256": catalog.get("catalog_sha256", canonical_hash(catalog)),
        "actors": [{"id": "a", "actor_type": "person", "span_id": span}],
        "decisions": [{"proposal_id": "p0", "actor_id": "a", "span_id": span}],
    }


def catalog():
    return span_catalog.build_catalog(
        source(), [u["id"] for u in source_units(source())], [], [{"id": "p0", "text": "A🙂"}]
    )


@pytest.mark.parametrize("mutation", ["duplicate", "missing", "foreign", "anchor", "hash"])
def test_inventory_rejects_corrupt_lineage(mutation):
    c = catalog()
    s = selection(c)
    if mutation == "duplicate":
        s["actors"].append(copy.deepcopy(s["actors"][0]))
    elif mutation == "missing":
        s["decisions"] = []
    elif mutation == "foreign":
        s["decisions"][0]["actor_id"] = "not-listed"
    elif mutation == "anchor":
        s["actors"][0]["span_id"] = c["proposal_ledger"][0]["span_ids"][1]
    else:
        s["catalog_sha256"] = "changed"
    with pytest.raises(DraftError):
        protocol.compile_inventory(source(), c, s, CONTRACT, PROVENANCE)


def test_unresolved_proposal_is_not_discarded_or_forced_to_an_actor():
    c = span_catalog.build_catalog(
        source(),
        [u["id"] for u in source_units(source())],
        [],
        [{"id": "p0", "text": "A🙂"}, {"id": "p1", "text": "missing"}],
    )
    s = selection(c)
    s["decisions"].append({"proposal_id": "p1", "actor_id": None, "span_id": None})
    result = protocol.compile_inventory(source(), c, s, CONTRACT, PROVENANCE)
    assert result["unresolved_proposals"] == ["p1"]
    assert not result["semantic_identity_verified"]


@pytest.mark.parametrize("version", [15, 16])
@pytest.mark.parametrize("failure", [None, "unresolved", "header_mutated"])
def test_fresh_inventory_runner_replay(workspace, failure, version):  # noqa: F811
    root, docs, config = prepare(workspace)
    for name in [
        "ontology/private-lending-v0.3.json",
        "schemas/extraction-v0.3.schema.json",
        f"prompts/qwen-extraction-v{version}.txt",
    ]:
        (root / name).write_bytes((ROOT / name).read_bytes())
    cfg = json.loads(config.read_text())
    cfg["version"] = f"qwen-extraction-v{version}"
    config.write_text(json.dumps(cfg))

    class Client(SimulatedClient):
        completed = 0

        def call(self, path, body=None, timeout=15):
            if path != "/v1/chat/completions":
                return super().call(path, body, timeout)
            self.paths.append(path)
            n = self.completed
            payload = json.loads(body["messages"][-1]["content"])
            if n == 0:
                value = {"forms": ["A🙂", "missing"] if failure == "unresolved" else ["A🙂"]}
            elif n == 1:
                value = selection(payload["catalog"])
                if failure == "unresolved":
                    value["decisions"].append(
                        {"proposal_id": "p1", "actor_id": None, "span_id": None}
                    )
            elif n == 2:
                # Obtain the exact frozen constant actually sent to the service.
                schema = body["response_format"]["schema"]
                value = fixtures()
                value["actors"] = copy.deepcopy(schema["properties"]["actors"]["const"])
                for c in value["contexts"]:
                    c["attributed_actor_ids"] = ["a"]
                if failure == "header_mutated":
                    value["actors"][0]["id"] = "changed"
            elif n in (3, 5):
                value = {"forms": []}
            else:
                value = {
                    "catalog_sha256": payload["catalog_sha256"],
                    "mentions": [],
                    "proposal_decisions": [],
                    "roles": [],
                    "events": [],
                    "statements": [],
                }
            self.completed += 1
            return response(
                {
                    "model": MODEL,
                    "choices": [
                        {"finish_reason": "stop", "message": {"content": json.dumps(value)}}
                    ],
                }
            )

    run = root / "artifacts/v15"
    result = run_extraction(root, [docs], config, run, Client())
    expected = {
        "unresolved": "inventory_unresolved",
        "header_mutated": "draft_grounding_failed",
        None: "accepted_candidate",
    }[failure]
    assert result["document_status_counts"] == {expected: 1}
    assert result["requests_submitted"] == {"unresolved": 2, "header_mutated": 3, None: 7}[failure]
    replay = replay_completed(root, [docs], run, root / "artifacts/replay")
    assert replay["requests_submitted"] == 0
    assert replay["outcome_and_candidate_equality_verified"]


def test_compact_aliases_preserve_every_position_and_full_catalog_hash():
    from judicial_kg.extraction.compact_inventory import compact_view

    c = catalog()
    before = canonical_hash(c)
    schema, view, aliases = compact_view(c, protocol.inventory_schema(c, CONTRACT))
    assert canonical_hash(c) == before == view["catalog_sha256"]
    assert len(aliases) == len(c["entries"])
    for entry, full in zip(view["entries"], c["entries"], strict=True):
        assert aliases[entry["span_id"]] == full["span_id"]
        assert {k: entry[k] for k in full["selector"]} == full["selector"]
    assert len(json.dumps(view)) < len(json.dumps(c))
    bad = selection(view)
    bad["actors"][0]["span_id"] = c["entries"][0]["span_id"]
    with pytest.raises(DraftError):
        protocol.context_first.check_schema(bad, schema)
