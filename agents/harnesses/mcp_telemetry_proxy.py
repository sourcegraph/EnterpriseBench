#!/usr/bin/env python3
"""Local-only MCP proxy that records Sourcegraph aggregate tool telemetry.

The proxy is staged into benchmark containers and runs as root. Agents can use
it, but cannot alter its 0600 JSONL trace. Authorization is forwarded in memory
and is never written to the trace.
"""

from __future__ import annotations

import hashlib
import json
import math
import os
import re
import threading
import urllib.error
import urllib.request
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any, Iterator, Sequence

DEFAULT_BIND = "127.0.0.1"
DEFAULT_PORT = 8765
MAX_REQUEST_BYTES = 2 * 1024 * 1024
MAX_RESPONSE_BYTES = 16 * 1024 * 1024
MAX_TRACE_BYTES = 64 * 1024 * 1024
TRACE_VERSION = 1
UPSTREAM_USER_AGENT = "enterprisebench-mcp-proxy/1.0"

_HOP_BY_HOP_HEADERS = frozenset(
    {
        "connection",
        "content-length",
        "accept-encoding",
        "host",
        "keep-alive",
        "proxy-authenticate",
        "proxy-authorization",
        "te",
        "trailer",
        "transfer-encoding",
        "upgrade",
    }
)
_SENSITIVE_KEYS = frozenset(
    {
        "accesstoken",
        "apikey",
        "authorization",
        "bearertoken",
        "cookie",
        "password",
        "secret",
        "token",
    }
)
_INNER_FIELDS = {
    "turns": "subAgentTurns",
    "duration_ms": "subAgentDurationMs",
    "tool_calls": "subAgentToolCalls",
    "total_input_tokens": "subAgentTotalInputTokens",
    "cached_tokens": "subAgentCachedTokens",
    "cache_creation_input_tokens": "subAgentCacheCreationInputTokens",
    "prompt_tokens": "subAgentPromptTokens",
    "completion_tokens": "subAgentCompletionTokens",
    "total_tokens": "subAgentTotalTokens",
}
_TRACE_LOCK = threading.Lock()

try:  # Staged beside this file in the container; absent means no treatment arm.
    import sourcegraph_server_treatment as _treatment
except ImportError:  # pragma: no cover - exercised by the in-repo import below
    try:
        from agents.harnesses import sourcegraph_server_treatment as _treatment
    except ImportError:
        _treatment = None  # type: ignore[assignment]


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _normalized_key(value: Any) -> str:
    return "".join(char for char in str(value).lower() if char.isalnum())


def redact_sensitive(value: Any, secret_values: tuple[str, ...] = ()) -> Any:
    """Return a recursively redacted copy of untrusted JSON."""
    if isinstance(value, dict):
        return {
            str(key): (
                "[REDACTED]"
                if _normalized_key(key) in _SENSITIVE_KEYS
                else redact_sensitive(item, secret_values)
            )
            for key, item in value.items()
        }
    if isinstance(value, list):
        return [redact_sensitive(item, secret_values) for item in value]
    if isinstance(value, str):
        redacted = value
        for secret in secret_values:
            if len(secret) >= 8:
                redacted = redacted.replace(secret, "[REDACTED]")
        return redacted
    return value


def iter_jsonrpc_payloads(body: bytes, content_type: str) -> Iterator[dict[str, Any]]:
    """Yield JSON-RPC objects from a plain JSON or SSE response body."""
    text = body.decode("utf-8", errors="replace")
    candidates: list[str]
    if "text/event-stream" in content_type.lower():
        candidates = [
            line.partition(":")[2].lstrip()
            for line in text.splitlines()
            if line.startswith("data:")
        ]
    else:
        candidates = [text]

    for candidate in candidates:
        try:
            decoded = json.loads(candidate)
        except json.JSONDecodeError:
            continue
        values = decoded if isinstance(decoded, list) else [decoded]
        yield from (value for value in values if isinstance(value, dict))


def apply_treatment(
    body: bytes,
    content_type: str,
    requests_by_id: dict[Any, dict[str, str | None]],
) -> bytes:
    """Rewrite one upstream response so it matches the patched Sourcegraph server.

    The agent must see the treated body, so this runs before the response is
    captured or forwarded. A treatment that cannot be applied raises, which the
    proxy turns into an error response: an arm quietly serving control text
    would report a null result as a real one.
    """
    if _treatment is None:
        raise ValueError("treatment arm requested but overlay module is missing")
    methods = {context.get("method") for context in requests_by_id.values()}
    if not methods & {"initialize", "tools/list"}:
        return body

    def treat(payload: dict[str, Any]) -> None:
        context = requests_by_id.get(_jsonrpc_id(payload.get("id")))
        if context is not None:
            _treatment.apply(payload, method=context.get("method") or "")

    text = body.decode("utf-8", errors="strict")
    is_sse = "text/event-stream" in content_type.lower()
    if not is_sse:
        payload = json.loads(text)
        for item in payload if isinstance(payload, list) else [payload]:
            if isinstance(item, dict):
                treat(item)
        return json.dumps(payload).encode()

    lines: list[str] = []
    for line in text.splitlines(keepends=True):
        stripped = line.rstrip("\r\n")
        if not stripped.startswith("data:"):
            lines.append(line)
            continue
        try:
            payload = json.loads(stripped.partition(":")[2].lstrip())
        except json.JSONDecodeError:
            lines.append(line)
            continue
        if isinstance(payload, dict):
            treat(payload)
        ending = line[len(stripped):]
        lines.append(f"data: {json.dumps(payload)}{ending}")
    return "".join(lines).encode()


def sanitize_request(
    payload: dict[str, Any],
    *,
    secret_values: tuple[str, ...] = (),
) -> dict[str, Any]:
    """Reduce one JSON-RPC request to the fields needed for benchmark evidence."""
    params = payload.get("params") if isinstance(payload.get("params"), dict) else {}
    arguments = (
        params.get("arguments") if isinstance(params.get("arguments"), dict) else {}
    )
    return {
        "trace_version": TRACE_VERSION,
        "direction": "request",
        "id": payload.get("id"),
        "method": payload.get("method"),
        "tool": params.get("name"),
        "arguments": redact_sensitive(arguments, secret_values),
    }


def _jsonrpc_id(value: Any) -> str | int | float | None:
    """Return a hashable JSON-RPC correlation id or reject malformed input."""
    if value is None or isinstance(value, str):
        return value
    if isinstance(value, int) and not isinstance(value, bool):
        return value
    if isinstance(value, float) and math.isfinite(value):
        return value
    raise ValueError("JSON-RPC id must be a finite string, number, or null")


def _canonical_sha256(value: Any) -> str:
    encoded = json.dumps(
        value,
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    ).encode()
    return hashlib.sha256(encoded).hexdigest()


def _tool_inventory_provenance(result: dict[str, Any]) -> dict[str, Any]:
    raw_tools = result.get("tools")
    if not isinstance(raw_tools, list):
        return {}
    tools = sorted(
        (
            {
                key: tool[key]
                for key in (
                    "name",
                    "description",
                    "inputSchema",
                    "outputSchema",
                    "annotations",
                )
                if key in tool
            }
            for tool in raw_tools
            if isinstance(tool, dict) and isinstance(tool.get("name"), str)
        ),
        key=lambda tool: tool["name"],
    )
    finder = next((tool for tool in tools if tool["name"] == "code_finder"), None)
    provenance: dict[str, Any] = {
        "tool_names": [tool["name"] for tool in tools],
        "tool_inventory_sha256": _canonical_sha256(tools),
    }
    if finder is not None:
        provenance["code_finder_schema_sha256"] = _canonical_sha256(
            {
                "inputSchema": finder.get("inputSchema"),
                "outputSchema": finder.get("outputSchema"),
            }
        )
    return provenance


def extract_response_meta(
    payload: dict[str, Any],
    *,
    method: str = "tools/call",
    tool: str | None,
    status: int,
) -> dict[str, Any]:
    """Capture result metadata without copying potentially large tool content."""
    result = payload.get("result") if isinstance(payload.get("result"), dict) else {}
    meta = result.get("_meta") if isinstance(result.get("_meta"), dict) else {}
    provenance: dict[str, Any] = {}
    if method == "initialize":
        protocol_version = result.get("protocolVersion")
        server_info = result.get("serverInfo")
        if isinstance(protocol_version, str):
            provenance["protocol_version"] = protocol_version
        if isinstance(server_info, dict):
            provenance["server_info"] = redact_sensitive(server_info)
    elif method == "tools/list":
        provenance = _tool_inventory_provenance(result)

    captured = {
        "trace_version": TRACE_VERSION,
        "direction": "response",
        "id": payload.get("id"),
        "method": method,
        "tool": tool,
        "status": status,
        "is_error": bool(
            status >= 400 or result.get("isError") or payload.get("error")
        ),
        "meta": redact_sensitive(meta),
    }
    if provenance:
        captured["provenance"] = provenance
    return captured


def _load_trace(trace_path: Path) -> list[dict[str, Any]]:
    records: list[dict[str, Any]] = []
    for line_number, line in enumerate(trace_path.read_text().splitlines(), start=1):
        if not line.strip():
            continue
        try:
            record = json.loads(line)
        except json.JSONDecodeError as exc:
            raise ValueError(
                f"{trace_path}:{line_number}: invalid MCP trace JSON"
            ) from exc
        if not isinstance(record, dict):
            raise ValueError(
                f"{trace_path}:{line_number}: trace event must be an object"
            )
        records.append(record)
    return records


def _nonnegative_number(value: Any) -> int | float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return 0
    if isinstance(value, float) and not math.isfinite(value):
        raise ValueError("Sourcegraph telemetry values must be finite")
    if value < 0:
        return 0
    return value


def _aggregate_inner(raw_meta: list[dict[str, Any]]) -> dict[str, int | float]:
    aggregate: dict[str, int | float] = {field: 0 for field in _INNER_FIELDS}
    context_values: list[int | float] = []
    for meta in raw_meta:
        sourcegraph = meta.get("sourcegraphToolTelemetry")
        if not isinstance(sourcegraph, dict):
            continue
        aggregate = {
            field: aggregate[field]
            + _nonnegative_number(sourcegraph.get(sourcegraph_field))
            for field, sourcegraph_field in _INNER_FIELDS.items()
        }
        context = _nonnegative_number(sourcegraph.get("subAgentContextUsagePercent"))
        context_values.append(context)
    aggregate["max_context_usage_percent"] = max(context_values, default=0)
    return aggregate


def _invalid_reason(
    *,
    mode: str,
    expected_repo_count: int,
    finder_calls: int,
    direct_calls: int,
    telemetry_responses: int,
    failed_finder_responses: int,
    repository_scope: dict[str, Any],
) -> str:
    if mode == "mcp_code_finder":
        # At least one call per repo, not exactly one. The ablation's meaning
        # rests on where retrieval happens, not on how many times the agent
        # asks; a second call against one repo is still Finder doing the work.
        # The `direct_calls` check below is what actually holds the arm.
        if finder_calls < expected_repo_count:
            return (
                f"expected at least {expected_repo_count} code_finder call(s), "
                f"observed {finder_calls}"
            )
        if direct_calls:
            return f"forced Finder arm made {direct_calls} direct retrieval call(s)"
        expected = repository_scope["expected"]
        if expected and (
            repository_scope["ambiguous_or_unscoped_calls"]
            or any(
                repository_scope["finder_calls_by_repo"].get(repository, 0) < 1
                for repository in expected
            )
        ):
            return "forced Finder repository scope missed at least one repo"
    elif mode == "mcp_assisted" and finder_calls < 1:
        return "assisted Finder arm made no code_finder call"
    if failed_finder_responses:
        return f"code_finder returned {failed_finder_responses} failed response(s)"
    if telemetry_responses != finder_calls:
        return (
            "code_finder aggregate telemetry missing: "
            f"{telemetry_responses}/{finder_calls} response(s) carried telemetry"
        )
    return ""


def _repository_scope(
    finder_requests: list[dict[str, Any]],
    expected_repositories: Sequence[str],
) -> dict[str, Any]:
    expected = list(dict.fromkeys(expected_repositories))
    calls_by_repo = {repository: 0 for repository in expected}
    ambiguous_or_unscoped = 0
    for request in finder_requests:
        arguments = request.get("arguments")
        task = arguments.get("task") if isinstance(arguments, dict) else None
        matches = (
            [
                repository
                for repository in expected
                if re.search(
                    rf"(?<![A-Za-z0-9._/-]){re.escape(repository)}"
                    rf"(?![A-Za-z0-9._/-])",
                    task,
                )
            ]
            if isinstance(task, str)
            else []
        )
        if len(matches) == 1:
            calls_by_repo[matches[0]] += 1
        else:
            ambiguous_or_unscoped += 1
    return {
        "expected": expected,
        "finder_calls_by_repo": calls_by_repo,
        "ambiguous_or_unscoped_calls": ambiguous_or_unscoped,
    }


def _trace_provenance(records: list[dict[str, Any]]) -> dict[str, Any]:
    timestamps = [
        record["timestamp"]
        for record in records
        if isinstance(record.get("timestamp"), str)
    ]
    versions = [
        record["trace_version"]
        for record in records
        if isinstance(record.get("trace_version"), int)
    ]
    provenance: dict[str, Any] = {
        "trace_version": max(versions, default=TRACE_VERSION),
    }
    if timestamps:
        provenance.update(
            {
                "trace_started_at": min(timestamps),
                "trace_finished_at": max(timestamps),
            }
        )
    for record in records:
        record_provenance = record.get("provenance")
        if (
            record.get("direction") == "response"
            and isinstance(record_provenance, dict)
        ):
            provenance.update(record_provenance)
    return provenance


def build_retrieval_telemetry(
    trace_path: Path,
    *,
    mode: str,
    expected_repo_count: int,
    outer_usage: dict[str, Any],
    expected_repositories: Sequence[str] = (),
) -> dict[str, Any]:
    """Summarize Finder behavior while preserving raw aggregate metadata."""
    records = _load_trace(trace_path)
    requests = [
        record
        for record in records
        if record.get("direction") == "request" and record.get("method") == "tools/call"
    ]
    finder_requests = [
        record for record in requests if record.get("tool") == "code_finder"
    ]
    finder_calls = len(finder_requests)
    direct_calls = sum(record.get("tool") != "code_finder" for record in requests)
    failed_finder_responses = sum(
        bool(record.get("is_error"))
        for record in records
        if record.get("direction") == "response" and record.get("tool") == "code_finder"
    )
    raw_meta = [
        record["meta"]
        for record in records
        if record.get("direction") == "response"
        and record.get("tool") == "code_finder"
        and isinstance(record.get("meta"), dict)
        and isinstance(record["meta"].get("sourcegraphToolTelemetry"), dict)
    ]
    inner = _aggregate_inner(raw_meta)
    repository_scope = _repository_scope(finder_requests, expected_repositories)
    outer_input = _nonnegative_number(outer_usage.get("total_input_tokens"))
    outer_output = _nonnegative_number(outer_usage.get("total_output_tokens"))
    reason = _invalid_reason(
        mode=mode,
        expected_repo_count=expected_repo_count,
        finder_calls=finder_calls,
        direct_calls=direct_calls,
        telemetry_responses=len(raw_meta),
        failed_finder_responses=failed_finder_responses,
        repository_scope=repository_scope,
    )
    return {
        "trace_captured": True,
        "valid": not reason,
        "invalid_reason": reason,
        "code_finder_calls": finder_calls,
        "direct_retrieval_calls": direct_calls,
        "repository_scope": repository_scope,
        "provenance": _trace_provenance(records),
        "inner": inner,
        "outer": {
            "input_tokens": outer_input,
            "output_tokens": outer_output,
            "total_tokens": outer_input + outer_output,
            "cost_usd": outer_usage.get("cost_usd"),
        },
        "combined": {
            "total_tokens": outer_input + outer_output + inner["total_tokens"],
            "cost_usd": None,
            "cost_note": (
                "Sourcegraph does not report Code Finder subagent cost in MCP _meta"
            ),
        },
        "raw_meta": raw_meta,
    }


def _append_trace(trace_path: Path, record: dict[str, Any]) -> None:
    trace_path.parent.mkdir(parents=True, exist_ok=True)
    encoded = json.dumps({**record, "timestamp": _utc_now()}, separators=(",", ":"))
    with _TRACE_LOCK:
        existing_size = trace_path.stat().st_size if trace_path.exists() else 0
        if existing_size + len(encoded.encode()) + 1 > MAX_TRACE_BYTES:
            raise OSError("MCP telemetry trace exceeded its size limit")
        with trace_path.open("a") as handle:
            handle.write(encoded + "\n")
        trace_path.chmod(0o600)


class TelemetryProxyHandler(BaseHTTPRequestHandler):
    """Forward MCP requests to the server-configured upstream."""

    server: "TelemetryProxyServer"

    def log_message(self, _format: str, *_args: Any) -> None:
        return

    def do_GET(self) -> None:  # noqa: N802 - BaseHTTPRequestHandler contract
        if self.path == "/healthz":
            self._send(200, b'{"status":"ok"}', {"Content-Type": "application/json"})
            return
        self._forward()

    def do_POST(self) -> None:  # noqa: N802 - BaseHTTPRequestHandler contract
        self._forward()

    def do_DELETE(self) -> None:  # noqa: N802 - BaseHTTPRequestHandler contract
        self._forward()

    def _read_body(self) -> bytes:
        raw_length = self.headers.get("Content-Length", "0")
        try:
            length = int(raw_length)
        except ValueError as exc:
            raise ValueError("invalid Content-Length") from exc
        if length < 0 or length > MAX_REQUEST_BYTES:
            raise ValueError("request body exceeds proxy limit")
        return self.rfile.read(length)

    def _forward_headers(self) -> dict[str, str]:
        forwarded = {
            name: value
            for name, value in self.headers.items()
            if name.lower() not in _HOP_BY_HOP_HEADERS and name.lower() != "user-agent"
        }
        return {**forwarded, "User-Agent": UPSTREAM_USER_AGENT}

    def _capture_requests(self, body: bytes) -> dict[Any, dict[str, str | None]]:
        requests_by_id: dict[Any, dict[str, str | None]] = {}
        content_type = self.headers.get("Content-Type", "application/json")
        authorization = self.headers.get("Authorization", "")
        credential = authorization.partition(" ")[2]
        secrets = tuple(value for value in (authorization, credential) if value)
        for payload in iter_jsonrpc_payloads(body, content_type):
            captured = sanitize_request(payload, secret_values=secrets)
            correlation_id = _jsonrpc_id(captured["id"])
            if correlation_id is not None:
                requests_by_id[correlation_id] = {
                    "method": captured["method"],
                    "tool": captured["tool"],
                }
            _append_trace(self.server.trace_path, captured)
        return requests_by_id

    def _capture_responses(
        self,
        body: bytes,
        content_type: str,
        status: int,
        requests_by_id: dict[Any, dict[str, str | None]],
    ) -> None:
        for payload in iter_jsonrpc_payloads(body, content_type):
            correlation_id = _jsonrpc_id(payload.get("id"))
            request_context = requests_by_id.get(correlation_id)
            if request_context is None:
                continue
            _append_trace(
                self.server.trace_path,
                extract_response_meta(
                    payload,
                    method=request_context["method"] or "",
                    tool=request_context["tool"],
                    status=status,
                ),
            )

    def _forward(self) -> None:
        try:
            body = self._read_body()
            requests_by_id = self._capture_requests(body) if body else {}
            request = urllib.request.Request(
                self.server.upstream_url,
                data=body if body else None,
                headers=self._forward_headers(),
                method=self.command,
            )
            try:
                with urllib.request.urlopen(
                    request, timeout=self.server.upstream_timeout
                ) as response:
                    status = response.status
                    headers = dict(response.headers.items())
                    response_body = response.read(MAX_RESPONSE_BYTES + 1)
            except urllib.error.HTTPError as exc:
                status = exc.code
                headers = dict(exc.headers.items())
                response_body = exc.read(MAX_RESPONSE_BYTES + 1)
            if len(response_body) > MAX_RESPONSE_BYTES:
                raise ValueError("upstream response exceeds proxy limit")
            content_type = headers.get("Content-Type", "application/json")
            if self.server.treatment_enabled:
                response_body = apply_treatment(
                    response_body, content_type, requests_by_id
                )
            self._capture_responses(
                response_body,
                content_type,
                status,
                requests_by_id,
            )
            self._send(status, response_body, headers)
        except (OSError, ValueError, urllib.error.URLError) as exc:
            error = json.dumps(
                {"error": {"code": -32000, "message": f"MCP proxy failure: {exc}"}}
            ).encode()
            self._send(502, error, {"Content-Type": "application/json"})

    def _send(self, status: int, body: bytes, headers: dict[str, str]) -> None:
        self.send_response(status)
        for name, value in headers.items():
            if name.lower() not in _HOP_BY_HOP_HEADERS:
                self.send_header(name, value)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)


class TelemetryProxyServer(ThreadingHTTPServer):
    """Threading server carrying immutable proxy configuration."""

    daemon_threads = True

    def __init__(
        self,
        address: tuple[str, int],
        *,
        upstream_url: str,
        trace_path: Path,
        upstream_timeout: int = 180,
        treatment_enabled: bool = False,
    ) -> None:
        self.upstream_url = upstream_url
        self.trace_path = trace_path
        self.upstream_timeout = upstream_timeout
        self.treatment_enabled = treatment_enabled
        super().__init__(address, TelemetryProxyHandler)


def main() -> int:
    upstream_url = os.environ.get("MCP_PROXY_UPSTREAM_URL", "").strip()
    trace_value = os.environ.get("MCP_PROXY_TRACE_PATH", "").strip()
    if not upstream_url or not trace_value:
        raise SystemExit("MCP_PROXY_UPSTREAM_URL and MCP_PROXY_TRACE_PATH are required")
    bind = os.environ.get("MCP_PROXY_BIND", DEFAULT_BIND)
    port = int(os.environ.get("MCP_PROXY_PORT", str(DEFAULT_PORT)))
    treatment_enabled = os.environ.get("MCP_PROXY_TREATMENT", "").strip() == "1"
    if treatment_enabled and _treatment is None:
        raise SystemExit("MCP_PROXY_TREATMENT=1 but the overlay module is missing")
    server = TelemetryProxyServer(
        (bind, port),
        upstream_url=upstream_url,
        trace_path=Path(trace_value),
        treatment_enabled=treatment_enabled,
    )
    server.serve_forever()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
