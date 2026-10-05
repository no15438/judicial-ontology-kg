"""Bounded SSH stdin bridge to existing Spark loopback HTTP; no remote files."""

import base64
import json
import re
import shlex
import subprocess
import time
from dataclasses import dataclass

from judicial_kg.validation_run import strict_json

# Request bodies travel on stdin, never in process arguments or shell interpolation.
WORKER = r"""
import base64,json,sys,urllib.request,urllib.error
command=json.load(sys.stdin)
body=command["body"]
request=urllib.request.Request(
    "http://127.0.0.1:"+str(command["port"])+command["path"],
    data=None if body is None else json.dumps(body,ensure_ascii=False).encode(),
    headers={"Content-Type":"application/json"})
try:
    with urllib.request.urlopen(request,timeout=command["timeout"]) as response:
        status,raw=response.status,response.read()
    print(json.dumps({"status":status,"body_base64":base64.b64encode(raw).decode()}))
except urllib.error.HTTPError as error:
    print(json.dumps({"status":error.code,"body_base64":base64.b64encode(error.read()).decode()}))
except Exception as error:
    print(json.dumps({"error_type":type(error).__name__}))
"""


@dataclass
class Response:
    status: int | None
    raw: bytes
    elapsed_seconds: float
    error: str | None = None
    diagnostics: bytes = b""

    def json(self):
        if self.error or self.status != 200:
            raise ValueError("transport_or_http_failure")
        return strict_json(self.raw)


class SparkClient:
    def __init__(self, host="spark-lan", port=8003):
        if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.-]*", host) or port != 8003:
            raise ValueError("Expected a LAN SSH alias and the existing Qwen port 8003")
        self.host, self.port = host, port

    def call(self, path: str, body=None, timeout=15) -> Response:
        allowed = {
            "/health",
            "/v1/models",
            "/props",
            "/metrics",
            "/apply-template",
            "/tokenize",
            "/v1/chat/completions",
        }
        if path not in allowed or not 0 < timeout <= 900:
            raise ValueError("Unsupported request route or timeout")
        return self._request(path, body, timeout)

    def _request(self, path, body, timeout, *, port=None):
        """Shared stdin bridge; public clients enforce their own fixed route allowlists."""
        request = {
            "port": self.port if port is None else port,
            "path": path,
            "body": body,
            "timeout": timeout,
        }
        command = [
            "ssh",
            "-T",
            "-o",
            "BatchMode=yes",
            "-o",
            "ConnectTimeout=10",
            "-o",
            "StrictHostKeyChecking=yes",
            self.host,
            "python3 -c " + shlex.quote(WORKER),
        ]
        started = time.monotonic()
        try:
            process = subprocess.run(
                command,
                input=json.dumps(request, ensure_ascii=False).encode(),
                capture_output=True,
                timeout=timeout + 15,
            )
        except subprocess.TimeoutExpired as error:
            return Response(
                None,
                error.stdout or b"",
                time.monotonic() - started,
                "ssh_timeout",
                error.stderr or b"",
            )
        except OSError:
            return Response(None, b"", time.monotonic() - started, "ssh_start_failed")
        elapsed = time.monotonic() - started
        if process.returncode:
            return Response(None, process.stdout, elapsed, "ssh_failed", process.stderr)
        try:
            envelope = strict_json(process.stdout)
            if "error_type" in envelope:
                return Response(
                    None, process.stdout, elapsed, "remote_request_failed", process.stderr
                )
            return Response(
                envelope["status"],
                base64.b64decode(envelope["body_base64"], validate=True),
                elapsed,
                diagnostics=process.stderr,
            )
        except (ValueError, KeyError, TypeError):
            return Response(
                None, process.stdout, elapsed, "bridge_invalid_response", process.stderr
            )
