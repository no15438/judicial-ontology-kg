"""Small, explicitly executed Bailian trials; no retries, uploads, or cloud storage."""

import http.client
import json
import os
import re
import ssl
import stat
import subprocess
import sys
import time
import urllib.error
import urllib.request
from collections import Counter
from datetime import UTC, datetime
from decimal import Decimal, InvalidOperation
from pathlib import Path
from uuid import uuid4

from judicial_kg.contracts import COLLECTIONS, Contract
from judicial_kg.extraction.draft import DraftError, draft_schema, materialize, messages
from judicial_kg.extraction.run import PROMPT_FILE, completion_content, usage_counts
from judicial_kg.extraction.transport import Response
from judicial_kg.sources.development import safe_output, write_json
from judicial_kg.sources.records import canonical_hash, sha256
from judicial_kg.validation import validate_record, validate_source
from judicial_kg.validation_run import strict_json, validate_batch

MODEL = "qwen3.8-max-0902"
PRICE_SOURCE = "https://help.aliyun.com/zh/model-studio/qwen3-8-max"


def base_url(value: str) -> str:
    if not isinstance(value, str) or not re.fullmatch(
        r"https://[a-z0-9-]+\.cn-beijing\.maas\.aliyuncs\.com/compatible-mode/v1", value
    ):
        raise ValueError("Expected the approved Beijing workspace HTTPS Base URL")
    return value


def credentials(root: Path) -> tuple[str, str]:
    """Only this project's ignored env file or process env; never execute shell text."""
    values = {}
    path = root / ".env.local"
    if path.is_symlink():
        raise ValueError("Refusing symlink credential file")
    if path.exists():
        if stat.S_IMODE(path.stat().st_mode) & 0o077:
            raise ValueError("Credential file must be private (chmod 600 .env.local)")
        for line in path.read_text().splitlines():
            if not line.strip() or line.lstrip().startswith("#"):
                continue
            name, sep, value = line.partition("=")
            if not sep or name not in {"DASHSCOPE_API_KEY", "BAILIAN_BASE_URL"} or name in values:
                raise ValueError("Unsupported or duplicate local credential setting")
            values[name] = value.strip()
    for name in ("DASHSCOPE_API_KEY", "BAILIAN_BASE_URL"):
        if os.environ.get(name):
            values[name] = os.environ[name]
    key = values.get("DASHSCOPE_API_KEY", "")
    if not re.fullmatch(r"sk-[^\s*]+", key):
        raise ValueError("DASHSCOPE_API_KEY is missing or invalid; never paste it into chat")
    return key, base_url(values.get("BAILIAN_BASE_URL", ""))


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        # Never forward Bearer credentials or source text to a redirected endpoint.
        return None


class BailianClient:
    def __init__(self, key: str, endpoint: str):
        self._key, self.endpoint = key, base_url(endpoint)
        context = ssl.create_default_context()
        # Python.org macOS builds can lack a bundled CA file. Add the OS trust
        # bundle without disabling verification or replacing explicit CA settings.
        if (
            sys.platform == "darwin"
            and not os.environ.get("SSL_CERT_FILE")
            and ssl.get_default_verify_paths().cafile is None
            and Path("/etc/ssl/cert.pem").is_file()
        ):
            context.load_verify_locations(cafile="/etc/ssl/cert.pem")
        self._opener = urllib.request.build_opener(
            NoRedirect(), urllib.request.HTTPSHandler(context=context)
        )

    def call(self, body: dict, timeout: int) -> Response:
        request = urllib.request.Request(
            self.endpoint + "/chat/completions",
            data=json.dumps(body, ensure_ascii=False).encode(),
            headers={"Authorization": "Bearer " + self._key, "Content-Type": "application/json"},
            method="POST",
        )
        started = time.monotonic()
        try:
            with self._opener.open(request, timeout=timeout) as response:
                return Response(response.status, response.read(), time.monotonic() - started)
        except urllib.error.HTTPError as error:
            return Response(error.code, error.read(), time.monotonic() - started)
        except http.client.IncompleteRead as error:
            return Response(None, error.partial, time.monotonic() - started, "transport_failed")
        except http.client.HTTPException:
            return Response(None, b"", time.monotonic() - started, "transport_failed")
        except urllib.error.URLError as error:
            code = (
                "tls_verification_failed"
                if isinstance(error.reason, ssl.SSLCertVerificationError)
                else "transport_failed"
            )
            return Response(None, b"", time.monotonic() - started, code)
        except (OSError, ValueError):
            # Exception messages can contain transport details; do not log them or retry.
            return Response(None, b"", time.monotonic() - started, "transport_failed")


def load_config(path: Path) -> dict:
    config = strict_json(path.read_bytes())
    bounds = {
        "max_input_reservation_tokens": (8192, 120000),
        "max_output_tokens": (128, 16384),
        "request_timeout_seconds": (10, 300),
        "max_documents": (1, 2),
        "limit_per_source": (1, 1),
    }
    keys = set(bounds) | {
        "version",
        "model",
        "temperature",
        "budget_cny",
        "input_cny_per_million",
        "output_cny_per_million",
    }
    if not isinstance(config, dict) or set(config) != keys:
        raise ValueError("Invalid Bailian configuration keys")
    for key, (low, high) in bounds.items():
        if type(config[key]) is not int or not low <= config[key] <= high:
            raise ValueError("Invalid Bailian bounded integer setting: " + key)
    if (
        config["version"] != "bailian-extraction-v1"
        or config["model"] != MODEL
        or type(config["temperature"]) not in (int, float)
        or config["temperature"] != 0.7
        or config["input_cny_per_million"] != "12"
        or config["output_cny_per_million"] != "36"
    ):
        raise ValueError("Unsupported model or price configuration; review before changing")
    try:
        if not isinstance(config["budget_cny"], str):
            raise ValueError("Budget must be a decimal string")
        budget = Decimal(config["budget_cny"])
        if not budget.is_finite() or not 0 < budget <= 5:
            raise ValueError("Budget must be positive and at most the approved 5 CNY")
    except (InvalidOperation, TypeError):
        raise ValueError("Invalid budget") from None
    return config


def cost(config: dict, input_tokens: int, output_tokens: int) -> Decimal:
    return (
        input_tokens * Decimal(config["input_cny_per_million"])
        + output_tokens * Decimal(config["output_cny_per_million"])
    ) / Decimal(1000000)


def run_bailian(
    root: Path,
    document_paths: list[Path],
    config_path: Path,
    output: Path,
    *,
    execute=False,
    client=None,
) -> dict:
    root, output = root.resolve(), safe_output(root, output)
    config, contract = load_config(config_path), Contract.load(root)
    prompt = (root / PROMPT_FILE).read_text()
    schema = draft_schema(contract)
    documents, inputs = [], []
    for path in document_paths:
        raw = path.read_bytes()
        docs = strict_json(raw)
        if not isinstance(docs, list):
            raise ValueError("Expected document arrays")
        documents.extend(docs)
        inputs.append({"sha256": sha256(raw), "bytes": len(raw)})
    validate_batch(documents, [], contract)
    for doc in documents:
        validate_source(doc)
    selected, counts = [], Counter()
    for index, doc in enumerate(documents):
        if len(selected) < config["max_documents"] and counts[doc["dataset"]] < 1:
            selected.append(index)
            counts[doc["dataset"]] += 1
    # Offline planning never loads a credential or creates an HTTP client.
    if execute and client is None:
        client = BailianClient(*credentials(root))
    output.mkdir(parents=True, exist_ok=False)
    run_id = uuid4().hex
    outcomes = [
        {
            "document_id": doc["document_id"],
            "selected": i in selected,
            "status": "pending" if i in selected else "not_selected",
            "issues": [],
        }
        for i, doc in enumerate(documents)
    ]
    metadata = {
        "schema_version": 1,
        "run_id": run_id,
        "run_kind": "bailian_development_extraction_not_gold",
        "execute": execute,
        "completed": False,
        "started_at_utc": datetime.now(UTC).isoformat(),
        "code_commit": subprocess.check_output(
            ["git", "rev-parse", "HEAD"], cwd=root, text=True
        ).strip(),
        "worktree_dirty": bool(subprocess.check_output(["git", "status", "--porcelain"], cwd=root)),
        "code_sha256": {
            str(p.relative_to(root)): sha256(p.read_bytes())
            for p in sorted((root / "src/judicial_kg").rglob("*.py"))
        },
        "dependency_lock_sha256": sha256((root / "uv.lock").read_bytes()),
        "config": config,
        "config_sha256": canonical_hash(config),
        "prompt_file": PROMPT_FILE,
        "prompt_sha256": sha256(prompt.encode()),
        "draft_schema_sha256": canonical_hash(schema),
        **contract.versions(),
        "input_files": inputs,
        "expected_documents": len(documents),
        "selected_documents": len(selected),
        "requests_submitted": 0,
        "price_source": PRICE_SOURCE,
        "price_verified_date": "2026-10-04",
        "provider": "aliyun_bailian",
        "region": "cn-beijing",
        "model_weight_hash_verified": False,
        "gold_available": False,
        "semantic_precision_recall_f1": None,
        "requires_user_legal_annotation": False,
        "billing_note": "List-price estimate, not a provider invoice or account-wide cap",
    }
    reservation = cost(config, config["max_input_reservation_tokens"], config["max_output_tokens"])
    reserved, measured = Decimal(0), Decimal(0)
    records, halted = [], False
    write_json(output / "config.json", config)
    (output / "prompt.txt").write_text(prompt)

    def checkpoint():
        write_json(output / "outcomes.json", outcomes)
        metadata.update(
            selected_outcomes=[o.copy() for o in outcomes if o["selected"]],
            document_status_counts=dict(sorted(Counter(o["status"] for o in outcomes).items())),
            reserved_cny=str(reserved),
            observed_usage_list_price_cny=str(measured),
            outcomes_sha256=sha256((output / "outcomes.json").read_bytes()),
        )
        write_json(output / "report.json", metadata)

    checkpoint()
    for index in selected:
        doc, outcome = documents[index], outcomes[index]
        if halted:
            outcome["status"] = "not_attempted_after_failure"
            continue
        try:
            request = {
                "model": config["model"],
                "messages": messages(doc, prompt, schema),
                "temperature": config["temperature"],
                "max_tokens": config["max_output_tokens"],
                "enable_thinking": False,
                "enable_search": False,
                "stream": False,
                "response_format": {"type": "json_object"},
            }
            # No tokenizer endpoint is assumed for cloud models. Bytes plus a generous
            # template allowance is a conservative guard, explicitly NOT measured tokens.
            byte_guard = sum(len(m["content"].encode()) for m in request["messages"]) + 4096
            outcome["input_utf8_bytes_plus_template_allowance"] = byte_guard
            if byte_guard > config["max_input_reservation_tokens"]:
                raise DraftError("input_reservation_exceeded")
            if reserved + reservation > Decimal(config["budget_cny"]):
                raise DraftError("budget_exceeded")
            write_json(output / f"request-{index:03}.json", request)
            if not execute:
                reserved += reservation
                outcome["status"] = "planned_not_submitted"
                continue
            reserved += reservation  # Keep the full reservation even on timeout or refusal.
            outcome["status"] = "in_flight"
            metadata["requests_submitted"] += 1
            checkpoint()
            response = client.call(request, config["request_timeout_seconds"])
            (output / f"response-{index:03}.bin").write_bytes(response.raw)
            outcome.update(
                http_status=response.status,
                transport_error=response.error,
                request_elapsed_seconds=response.elapsed_seconds,
                response_sha256=sha256(response.raw),
            )
            if response.error or response.status != 200:
                halted = True
                raise DraftError("transport_failed" if response.error else "http_failed")
            payload = response.json()
            usage = usage_counts(payload)
            outcome["usage"] = usage
            if not {"prompt_tokens", "completion_tokens"} <= usage.keys():
                halted = True
                raise DraftError("usage_missing")
            observed = cost(config, usage["prompt_tokens"], usage["completion_tokens"])
            measured += observed
            outcome["usage_list_price_cny"] = str(observed)
            if (
                usage["prompt_tokens"] > config["max_input_reservation_tokens"]
                or usage["completion_tokens"] > config["max_output_tokens"]
            ):
                halted = True
                raise DraftError("provider_usage_exceeds_reservation")
            content, _ = completion_content(payload, MODEL)
            record = materialize(
                content,
                doc,
                contract,
                {
                    "method": "model",
                    "producer": MODEL,
                    "version": config["version"],
                    "run_id": run_id,
                },
            )
            write_json(output / f"candidate-{index:03}.json", record)
            records.append(record)
            issues = validate_record(record, doc, contract)
            outcome["issues"] = issues
            outcome["status"] = (
                "contract_rejected"
                if issues
                else "accepted_candidate"
                if any(record[k] for k in COLLECTIONS)
                else "accepted_empty"
            )
        except (DraftError, ValueError, KeyError, TypeError, AttributeError) as error:
            code = error.code if isinstance(error, DraftError) else "protocol_invalid_response"
            outcome.update(
                status=code, issues=[{"code": code, "path": getattr(error, "path", "/")}]
            )
            if code == "protocol_invalid_response":
                halted = True  # An unaccounted response may still have incurred a charge.
        checkpoint()
    lines = [json.dumps(record, ensure_ascii=False) for record in records]
    (output / "candidates.jsonl").write_text("".join(line + "\n" for line in lines))
    validation = validate_batch(documents, lines, contract)
    write_json(output / "accepted.json", validation["accepted_records"])
    write_json(
        output / "validation-ledger.json",
        {k: validation[k] for k in ("document_outcomes", "attempts")},
    )
    metadata["validation"] = validation["summary"]
    metadata["outputs_sha256"] = {
        p.name: sha256(p.read_bytes())
        for p in sorted(output.iterdir())
        if p.is_file() and p.name not in {"report.json", "outcomes.json"}
    }
    metadata.update(completed=True, finished_at_utc=datetime.now(UTC).isoformat())
    checkpoint()
    return metadata
