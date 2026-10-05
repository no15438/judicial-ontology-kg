"""Reuse a hash-verified request prefix, never bypassing downstream validation."""

from pathlib import Path

from judicial_kg.sources.records import sha256
from judicial_kg.validation_run import strict_json


class StagePrefix:
    """Exact request dependencies are checked sequentially; any miss invalidates the suffix.

    Completed HTTP responses are reusable inputs, not accepted facts. The caller reruns
    all schema, grounding and contract checks. The restart stage and all descendants
    are regenerated. No cross-document lookup or approximate request match is allowed.
    """

    def __init__(self, root, source, inputs, contract, config, restart_stage=None):
        self.source = source.resolve()
        raw = (self.source / "report.json").read_bytes()
        original = strict_json(raw)
        if not original["completed"] or original["status"] == "accepted_candidate":
            raise ValueError("Resume requires a completed unsuccessful run")
        if original["input_files"] != inputs or original["contract"] != contract:
            raise ValueError("Resume source or contract changed")
        for key in ("model", "temperature", "seed", "max_tokens", "response_mode"):
            if original["config"][key] != config[key]:
                raise ValueError("Resume inference configuration changed")
        for name, digest in original["outputs_sha256"].items():
            if Path(name).name != name or sha256((self.source / name).read_bytes()) != digest:
                raise ValueError("Resume artifact changed")
        stages = original["stages"]
        names = [s["stage"] for s in stages]
        if not names or len(set(names)) != len(names):
            raise ValueError("Resume stages must be nonempty and unique")
        self.restart_stage = restart_stage or names[-1]
        if self.restart_stage not in names:
            raise ValueError("Unknown restart stage")
        self.rows = stages[: names.index(self.restart_stage)]
        self.index = 0
        self.stop_reason = None
        self.origin = {
            "path": str(self.source.relative_to(root)),
            "sha256": sha256(raw),
            "committed_cny": original["budget_committed_cny"],
        }
        self.hashes = original["outputs_sha256"]

    def read(self, name):
        raw = (self.source / name).read_bytes()
        if name not in self.hashes or sha256(raw) != self.hashes[name]:
            raise ValueError("Resume artifact changed or unregistered")
        return raw

    def take(self, stage, request, schema):
        if self.stop_reason is not None:
            return None
        if self.index >= len(self.rows):
            self.stop_reason = "restart_boundary"
            return None
        row = self.rows[self.index]
        if row["stage"] != stage:
            self.stop_reason = "stage_order_changed"
            return None
        if row["status"] not in ("response_complete", "response_reused"):
            self.stop_reason = "source_response_not_complete"
            return None
        if (
            strict_json(self.read("request-" + stage + ".json")) != request
            or strict_json(self.read("schema-" + stage + ".json")) != schema
        ):
            self.stop_reason = "request_dependencies_changed"
            return None
        raw = self.read("response-" + stage + ".bin")
        receipt = strict_json(self.read("receipt-" + stage + ".json"))
        if receipt["status"] != 200 or receipt["error"] or sha256(raw) != row["response_sha256"]:
            raise ValueError("Resume response receipt invalid")
        self.index += 1
        return raw, receipt, row
