"""Tests for provider-neutral MCP telemetry capture and Finder validity."""

from __future__ import annotations

import json
import threading
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest

from agents.harnesses import mcp_telemetry_proxy, sourcegraph_server_treatment
from agents.harnesses.mcp_telemetry_proxy import (
    TelemetryProxyServer,
    _append_trace,
    _jsonrpc_id,
    build_retrieval_telemetry,
    extract_response_meta,
    iter_jsonrpc_payloads,
    redact_sensitive,
    sanitize_request,
)


def _request(call_id: int, tool: str, task: str = "find the Flask failure") -> dict:
    return {
        "timestamp": "2026-07-25T12:00:00Z",
        "direction": "request",
        "id": call_id,
        "method": "tools/call",
        "tool": tool,
        "arguments": {"task": task},
    }


def _response(call_id: int, *, with_meta: bool = True) -> dict:
    meta = {
        "sourcegraphToolTelemetry": {
            "subAgentTurns": 4,
            "subAgentDurationMs": 1250,
            "subAgentToolCalls": 7,
            "subAgentTotalInputTokens": 1200,
            "subAgentCachedTokens": 300,
            "subAgentCacheCreationInputTokens": 0,
            "subAgentPromptTokens": 900,
            "subAgentCompletionTokens": 200,
            "subAgentTotalTokens": 1400,
            "subAgentContextUsagePercent": 12.5,
        }
    }
    return {
        "timestamp": "2026-07-25T12:00:01Z",
        "direction": "response",
        "id": call_id,
        "tool": "code_finder",
        "status": 200,
        "is_error": False,
        "meta": meta if with_meta else {},
    }


def test_iter_jsonrpc_payloads_accepts_json_and_sse() -> None:
    payload = {"jsonrpc": "2.0", "id": 7, "result": {"content": []}}
    encoded = json.dumps(payload).encode()

    assert list(iter_jsonrpc_payloads(encoded, "application/json")) == [payload]
    assert list(
        iter_jsonrpc_payloads(
            b"event: message\ndata: " + encoded + b"\n\n",
            "text/event-stream",
        )
    ) == [payload]


def test_invalid_json_is_ignored_and_nested_sensitive_lists_are_redacted() -> None:
    assert list(iter_jsonrpc_payloads(b"not-json", "application/json")) == []
    assert redact_sensitive([{"token": "secret"}, "safe"]) == [
        {"token": "[REDACTED]"},
        "safe",
    ]


def test_extract_response_meta_keeps_raw_sourcegraph_aggregate() -> None:
    payload = {
        "jsonrpc": "2.0",
        "id": 7,
        "result": {
            "content": [{"type": "text", "text": "answer"}],
            "_meta": {
                "sourcegraphToolTelemetry": {
                    "subAgentTurns": 2,
                    "subAgentTotalTokens": 44,
                }
            },
        },
    }

    captured = extract_response_meta(payload, tool="code_finder", status=200)

    assert captured["id"] == 7
    assert captured["tool"] == "code_finder"
    assert captured["meta"] == payload["result"]["_meta"]
    assert "content" not in captured


def test_http_error_status_marks_tool_response_as_failed() -> None:
    payload = {
        "jsonrpc": "2.0",
        "id": 7,
        "result": {
            "_meta": {
                "sourcegraphToolTelemetry": {
                    "subAgentTurns": 2,
                    "subAgentTotalTokens": 44,
                }
            },
        },
    }

    captured = extract_response_meta(payload, tool="code_finder", status=500)

    assert captured["is_error"] is True


def test_extracts_structural_initialize_and_tool_inventory_provenance() -> None:
    initialize = {
        "jsonrpc": "2.0",
        "id": 1,
        "result": {
            "protocolVersion": "2025-06-18",
            "serverInfo": {"name": "sourcegraph", "version": "1.2.3"},
        },
    }
    tools = {
        "jsonrpc": "2.0",
        "id": 2,
        "result": {
            "tools": [
                {
                    "name": "read_file",
                    "description": "Read one file",
                    "inputSchema": {"type": "object"},
                },
                {
                    "name": "code_finder",
                    "description": "Find relevant code",
                    "inputSchema": {
                        "type": "object",
                        "properties": {"task": {"type": "string"}},
                        "required": ["task"],
                    },
                    "outputSchema": {
                        "type": "object",
                        "properties": {"text": {"type": "string"}},
                    },
                },
            ]
        },
    }

    init_capture = extract_response_meta(
        initialize,
        method="initialize",
        tool=None,
        status=200,
    )
    tools_capture = extract_response_meta(
        tools,
        method="tools/list",
        tool=None,
        status=200,
    )

    assert init_capture["provenance"] == {
        "protocol_version": "2025-06-18",
        "server_info": {"name": "sourcegraph", "version": "1.2.3"},
    }
    provenance = tools_capture["provenance"]
    assert provenance["tool_names"] == ["code_finder", "read_file"]
    assert len(provenance["tool_inventory_sha256"]) == 64
    assert len(provenance["code_finder_schema_sha256"]) == 64
    assert "tools" not in tools_capture


def test_tool_inventory_hash_is_order_independent() -> None:
    first = {
        "jsonrpc": "2.0",
        "id": 2,
        "result": {
            "tools": [
                {"name": "read_file", "inputSchema": {"type": "object"}},
                {"name": "code_finder", "inputSchema": {"type": "object"}},
            ]
        },
    }
    second = {
        **first,
        "result": {"tools": list(reversed(first["result"]["tools"]))},
    }

    left = extract_response_meta(
        first, method="tools/list", tool=None, status=200
    )
    right = extract_response_meta(
        second, method="tools/list", tool=None, status=200
    )

    assert (
        left["provenance"]["tool_inventory_sha256"]
        == right["provenance"]["tool_inventory_sha256"]
    )


def test_sanitize_request_never_records_headers_or_credentials() -> None:
    payload = {
        "jsonrpc": "2.0",
        "id": 3,
        "method": "tools/call",
        "params": {
            "name": "code_finder",
            "arguments": {
                "query": "trace it",
                "Authorization": "token should-never-land-in-trace",
            },
        },
    }

    captured = sanitize_request(payload)

    serialized = json.dumps(captured)
    assert captured["tool"] == "code_finder"
    assert captured["arguments"]["Authorization"] == "[REDACTED]"
    assert "should-never-land-in-trace" not in serialized


def test_forced_finder_requires_exactly_one_call_per_repo_and_no_direct_tools(
    tmp_path: Path,
) -> None:
    trace = tmp_path / "mcp_trace.jsonl"
    records = [_request(1, "code_finder"), _response(1)]
    trace.write_text("\n".join(json.dumps(record) for record in records) + "\n")

    telemetry = build_retrieval_telemetry(
        trace,
        mode="mcp_code_finder",
        expected_repo_count=1,
        outer_usage={
            "total_input_tokens": 100,
            "total_output_tokens": 25,
            "cost_usd": 0.2,
        },
    )

    assert telemetry["valid"] is True
    assert telemetry["code_finder_calls"] == 1
    assert telemetry["direct_retrieval_calls"] == 0
    assert telemetry["inner"]["turns"] == 4
    assert telemetry["inner"]["tool_calls"] == 7
    assert telemetry["inner"]["total_tokens"] == 1400
    assert telemetry["combined"]["total_tokens"] == 1525
    assert telemetry["combined"]["cost_usd"] is None
    assert telemetry["raw_meta"] == [records[1]["meta"]]


def test_forced_finder_requires_one_uniquely_scoped_call_per_expected_repo(
    tmp_path: Path,
) -> None:
    flask = "github.com/sg-evals/flask--2.3.3"
    werkzeug = "github.com/sg-evals/werkzeug--2.3.7"
    trace = tmp_path / "mcp_trace.jsonl"
    records = [
        _request(1, "code_finder", f"In {flask}, find context handling"),
        _response(1),
        _request(2, "code_finder", f"In {werkzeug}, find local proxy handling"),
        _response(2),
    ]
    trace.write_text("\n".join(json.dumps(record) for record in records) + "\n")

    telemetry = build_retrieval_telemetry(
        trace,
        mode="mcp_code_finder",
        expected_repo_count=2,
        expected_repositories=[flask, werkzeug],
        outer_usage={},
    )

    assert telemetry["valid"] is True
    assert telemetry["repository_scope"] == {
        "expected": [flask, werkzeug],
        "finder_calls_by_repo": {flask: 1, werkzeug: 1},
        "ambiguous_or_unscoped_calls": 0,
    }


def test_forced_finder_rejects_duplicate_repo_scope_even_when_count_matches(
    tmp_path: Path,
) -> None:
    flask = "github.com/sg-evals/flask--2.3.3"
    werkzeug = "github.com/sg-evals/werkzeug--2.3.7"
    trace = tmp_path / "mcp_trace.jsonl"
    records = [
        _request(1, "code_finder", f"In {flask}, find context handling"),
        _response(1),
        _request(2, "code_finder", f"In {flask}, find more context handling"),
        _response(2),
    ]
    trace.write_text("\n".join(json.dumps(record) for record in records) + "\n")

    telemetry = build_retrieval_telemetry(
        trace,
        mode="mcp_code_finder",
        expected_repo_count=2,
        expected_repositories=[flask, werkzeug],
        outer_usage={},
    )

    assert telemetry["valid"] is False
    assert "repository scope" in telemetry["invalid_reason"]
    assert telemetry["repository_scope"]["finder_calls_by_repo"][flask] == 2
    assert telemetry["repository_scope"]["finder_calls_by_repo"][werkzeug] == 0


def test_forced_finder_rejects_extended_wrong_mirror_name(tmp_path: Path) -> None:
    flask = "github.com/sg-evals/flask--3.1.0"
    trace = tmp_path / "mcp_trace.jsonl"
    records = [
        _request(
            1,
            "code_finder",
            f"In {flask}.1, inspect routing behavior",
        ),
        _response(1),
    ]
    trace.write_text("\n".join(json.dumps(record) for record in records) + "\n")

    telemetry = build_retrieval_telemetry(
        trace,
        mode="mcp_code_finder",
        expected_repo_count=1,
        expected_repositories=[flask],
        outer_usage={},
    )

    assert telemetry["valid"] is False
    assert telemetry["repository_scope"]["ambiguous_or_unscoped_calls"] == 1


def test_retrieval_telemetry_persists_server_provenance_and_trace_window(
    tmp_path: Path,
) -> None:
    trace = tmp_path / "mcp_trace.jsonl"
    records = [
        {
            "timestamp": "2026-07-25T12:00:00Z",
            "direction": "response",
            "id": 0,
            "method": "initialize",
            "tool": None,
            "status": 200,
            "is_error": False,
            "meta": {},
            "provenance": {
                "protocol_version": "2025-06-18",
                "server_info": {"name": "sourcegraph", "version": "1.2.3"},
            },
        },
        {
            "timestamp": "2026-07-25T12:00:01Z",
            "direction": "response",
            "id": 1,
            "method": "tools/list",
            "tool": None,
            "status": 200,
            "is_error": False,
            "meta": {},
            "provenance": {
                "tool_names": ["code_finder", "read_file"],
                "tool_inventory_sha256": "a" * 64,
                "code_finder_schema_sha256": "b" * 64,
            },
        },
        _request(2, "code_finder"),
        _response(2),
    ]
    trace.write_text("\n".join(json.dumps(record) for record in records) + "\n")

    telemetry = build_retrieval_telemetry(
        trace,
        mode="mcp_assisted",
        expected_repo_count=1,
        outer_usage={},
    )

    assert telemetry["provenance"] == {
        "trace_version": 1,
        "trace_started_at": "2026-07-25T12:00:00Z",
        "trace_finished_at": "2026-07-25T12:00:01Z",
        "protocol_version": "2025-06-18",
        "server_info": {"name": "sourcegraph", "version": "1.2.3"},
        "tool_names": ["code_finder", "read_file"],
        "tool_inventory_sha256": "a" * 64,
        "code_finder_schema_sha256": "b" * 64,
    }


def test_forced_finder_rejects_direct_sourcegraph_calls(tmp_path: Path) -> None:
    trace = tmp_path / "mcp_trace.jsonl"
    records = [
        _request(1, "code_finder"),
        _response(1),
        _request(2, "read_file"),
    ]
    trace.write_text("\n".join(json.dumps(record) for record in records) + "\n")

    telemetry = build_retrieval_telemetry(
        trace,
        mode="mcp_code_finder",
        expected_repo_count=1,
        outer_usage={},
    )

    assert telemetry["valid"] is False
    assert telemetry["direct_retrieval_calls"] == 1
    assert "direct retrieval" in telemetry["invalid_reason"]


def test_assisted_finder_allows_targeted_follow_up_tools(tmp_path: Path) -> None:
    trace = tmp_path / "mcp_trace.jsonl"
    records = [
        _request(1, "code_finder"),
        _response(1),
        _request(2, "read_file"),
    ]
    trace.write_text("\n".join(json.dumps(record) for record in records) + "\n")

    telemetry = build_retrieval_telemetry(
        trace,
        mode="mcp_assisted",
        expected_repo_count=1,
        outer_usage={},
    )

    assert telemetry["valid"] is True
    assert telemetry["code_finder_calls"] == 1
    assert telemetry["direct_retrieval_calls"] == 1


def test_finder_run_without_sourcegraph_meta_is_invalid(tmp_path: Path) -> None:
    trace = tmp_path / "mcp_trace.jsonl"
    records = [_request(1, "code_finder"), _response(1, with_meta=False)]
    trace.write_text("\n".join(json.dumps(record) for record in records) + "\n")

    telemetry = build_retrieval_telemetry(
        trace,
        mode="mcp_assisted",
        expected_repo_count=1,
        outer_usage={},
    )

    assert telemetry["valid"] is False
    assert "telemetry" in telemetry["invalid_reason"]


def test_finder_error_response_is_invalid_even_when_it_has_meta(tmp_path: Path) -> None:
    trace = tmp_path / "mcp_trace.jsonl"
    response = {**_response(1), "is_error": True}
    records = [_request(1, "code_finder"), response]
    trace.write_text("\n".join(json.dumps(record) for record in records) + "\n")

    telemetry = build_retrieval_telemetry(
        trace,
        mode="mcp_assisted",
        expected_repo_count=1,
        outer_usage={},
    )

    assert telemetry["valid"] is False
    assert "failed" in telemetry["invalid_reason"]


@pytest.mark.parametrize(
    "non_finite",
    [float("nan"), float("inf"), float("-inf")],
)
def test_non_finite_sourcegraph_telemetry_fails_loudly(
    tmp_path: Path,
    non_finite: float,
) -> None:
    trace = tmp_path / "mcp_trace.jsonl"
    response = _response(1)
    response["meta"]["sourcegraphToolTelemetry"][
        "subAgentTotalTokens"
    ] = non_finite
    records = [_request(1, "code_finder"), response]
    trace.write_text("\n".join(json.dumps(record) for record in records) + "\n")

    with pytest.raises(ValueError, match="finite"):
        build_retrieval_telemetry(
            trace,
            mode="mcp_assisted",
            expected_repo_count=1,
            outer_usage={},
        )


def test_large_integer_sourcegraph_telemetry_is_preserved(tmp_path: Path) -> None:
    trace = tmp_path / "mcp_trace.jsonl"
    response = _response(1)
    large_count = 10**1000
    response["meta"]["sourcegraphToolTelemetry"][
        "subAgentTotalTokens"
    ] = large_count
    records = [_request(1, "code_finder"), response]
    trace.write_text("\n".join(json.dumps(record) for record in records) + "\n")

    telemetry = build_retrieval_telemetry(
        trace,
        mode="mcp_assisted",
        expected_repo_count=1,
        outer_usage={},
    )

    assert telemetry["inner"]["total_tokens"] == large_count


@pytest.mark.parametrize(
    "value",
    [[], {}, True, float("nan"), float("inf"), float("-inf")],
)
def test_malformed_jsonrpc_ids_fail_as_controlled_value_errors(value: object) -> None:
    with pytest.raises(ValueError, match="JSON-RPC id"):
        _jsonrpc_id(value)


def test_arbitrarily_large_integer_jsonrpc_id_remains_hashable() -> None:
    large_id = 10**1000

    assert _jsonrpc_id(large_id) == large_id


def test_malformed_trace_fails_loudly_with_line_number(tmp_path: Path) -> None:
    trace = tmp_path / "mcp_trace.jsonl"
    trace.write_text('{"direction":"request"}\nnot-json\n')

    try:
        build_retrieval_telemetry(
            trace,
            mode="mcp_assisted",
            expected_repo_count=1,
            outer_usage={},
        )
    except ValueError as exc:
        assert f"{trace}:2" in str(exc)
    else:
        raise AssertionError("malformed telemetry trace must fail")


def test_trace_file_has_a_hard_size_limit(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    trace = tmp_path / "mcp_trace.jsonl"
    trace.write_text("already-full")
    monkeypatch.setattr(mcp_telemetry_proxy, "MAX_TRACE_BYTES", 1)

    with pytest.raises(OSError, match="size limit"):
        _append_trace(trace, {"direction": "request"})


def test_proxy_forwards_auth_but_never_persists_it(tmp_path: Path) -> None:
    received: dict[str, str] = {}

    class FakeUpstream(BaseHTTPRequestHandler):
        def log_message(self, _format: str, *_args: object) -> None:
            return

        def do_POST(self) -> None:  # noqa: N802
            received["authorization"] = self.headers.get("Authorization", "")
            received["user_agent"] = self.headers.get("User-Agent", "")
            length = int(self.headers["Content-Length"])
            request_payload = json.loads(self.rfile.read(length))
            response_payload = {
                "jsonrpc": "2.0",
                "id": request_payload["id"],
                "result": {
                    "content": [{"type": "text", "text": "found"}],
                    "_meta": {
                        "sourcegraphToolTelemetry": {
                            "subAgentTurns": 1,
                            "subAgentTotalTokens": 10,
                        }
                    },
                },
            }
            encoded = json.dumps(response_payload).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(encoded)))
            self.end_headers()
            self.wfile.write(encoded)

    upstream = ThreadingHTTPServer(("127.0.0.1", 0), FakeUpstream)
    upstream_thread = threading.Thread(target=upstream.serve_forever, daemon=True)
    upstream_thread.start()
    trace = tmp_path / "proxy.jsonl"
    proxy = TelemetryProxyServer(
        ("127.0.0.1", 0),
        upstream_url=f"http://127.0.0.1:{upstream.server_port}/mcp",
        trace_path=trace,
    )
    proxy_thread = threading.Thread(target=proxy.serve_forever, daemon=True)
    proxy_thread.start()
    token = "token sourcegraph-secret-value"
    body = json.dumps(
        {
            "jsonrpc": "2.0",
            "id": 1,
            "method": "tools/call",
            "params": {
                "name": "code_finder",
                "arguments": {
                    "query": (
                        "find Flask blueprint registration; accidental "
                        "credential sourcegraph-secret-value"
                    )
                },
            },
        }
    ).encode()
    request = urllib.request.Request(
        f"http://127.0.0.1:{proxy.server_port}/mcp",
        data=body,
        headers={
            "Authorization": token,
            "Content-Type": "application/json",
            "Accept": "application/json, text/event-stream",
        },
        method="POST",
    )

    try:
        with urllib.request.urlopen(request, timeout=2) as response:
            assert response.status == 200
            assert (
                json.loads(response.read())["result"]["content"][0]["text"] == "found"
            )
    finally:
        proxy.shutdown()
        upstream.shutdown()
        proxy.server_close()
        upstream.server_close()

    assert received["authorization"] == token
    assert received["user_agent"] == "enterprisebench-mcp-proxy/1.0"
    trace_text = trace.read_text()
    assert "sourcegraph-secret-value" not in trace_text
    records = [json.loads(line) for line in trace_text.splitlines()]
    assert records[0]["tool"] == "code_finder"
    assert records[1]["meta"]["sourcegraphToolTelemetry"]["subAgentTurns"] == 1


class TestServerTreatmentOverlay:
    """The mcp_treatment arm must serve the branch's text, or fail loudly."""

    @staticmethod
    def _tools_body() -> dict:
        return {
            "jsonrpc": "2.0",
            "id": 7,
            "result": {
                "tools": [
                    {
                        "name": name,
                        "description": " " + description,
                    }
                    for name, description, _replacement in (
                        sourcegraph_server_treatment.DESCRIPTION_REWRITES
                    )
                ]
            },
        }

    @staticmethod
    def _context(method: str) -> dict:
        return {7: {"method": method, "tool": None}}

    def test_tools_list_descriptions_are_replaced(self) -> None:
        body = json.dumps(self._tools_body()).encode()
        treated = json.loads(
            mcp_telemetry_proxy.apply_treatment(
                body, "application/json", self._context("tools/list")
            )
        )
        by_name = {tool["name"]: tool["description"] for tool in treated["result"]["tools"]}
        assert "runs its own multi-round search" in by_name["code_finder"]
        assert "list_repos, list_files, code_finder," in by_name["read_file"]
        assert "Use code_finder instead" in by_name["keyword_search"]

    def test_initialize_carries_the_server_instructions(self) -> None:
        body = json.dumps({"jsonrpc": "2.0", "id": 7, "result": {}}).encode()
        treated = json.loads(
            mcp_telemetry_proxy.apply_treatment(
                body, "application/json", self._context("initialize")
            )
        )
        assert (
            treated["result"]["instructions"]
            == sourcegraph_server_treatment.INSTRUCTIONS
        )

    def test_sse_framing_survives_the_rewrite(self) -> None:
        body = (
            b"event: message\ndata: "
            + json.dumps(self._tools_body()).encode()
            + b"\n\n"
        )
        treated = mcp_telemetry_proxy.apply_treatment(
            body, "text/event-stream", self._context("tools/list")
        ).decode()
        assert treated.startswith("event: message\n")
        assert treated.endswith("\n\n")
        payload = json.loads(treated.splitlines()[1].partition(":")[2])
        assert "runs its own multi-round search" in payload["result"]["tools"][0]["description"]

    def test_a_moved_anchor_raises_rather_than_serving_control_text(self) -> None:
        payload = self._tools_body()
        payload["result"]["tools"][0]["description"] = "something else entirely"
        with pytest.raises(sourcegraph_server_treatment.TreatmentError):
            mcp_telemetry_proxy.apply_treatment(
                json.dumps(payload).encode(),
                "application/json",
                self._context("tools/list"),
            )

    def test_a_missing_tool_raises(self) -> None:
        payload = self._tools_body()
        payload["result"]["tools"] = payload["result"]["tools"][1:]
        with pytest.raises(sourcegraph_server_treatment.TreatmentError):
            mcp_telemetry_proxy.apply_treatment(
                json.dumps(payload).encode(),
                "application/json",
                self._context("tools/list"),
            )

    def test_other_methods_pass_through_untouched(self) -> None:
        body = json.dumps({"jsonrpc": "2.0", "id": 7, "result": {"x": 1}}).encode()
        assert (
            mcp_telemetry_proxy.apply_treatment(
                body, "application/json", self._context("tools/call")
            )
            == body
        )


class TestTreatmentVariants:
    """Each decomposed arm applies its own half of the branch, and only that."""

    @staticmethod
    def _context(method: str, tool: str | None = None) -> dict:
        return {7: {"method": method, "tool": tool}}

    @staticmethod
    def _search(times: int = 1) -> dict:
        """Run *times* direct searches, returning the last treated payload."""
        treated: dict = {}
        for _ in range(times):
            treated = TestTreatmentVariants._treat(
                {
                    "jsonrpc": "2.0",
                    "id": 7,
                    "result": {
                        "content": [{"text": "hits"}],
                        "structuredContent": {"text": "hits"},
                    },
                },
                "tools/call",
                "nudge",
                tool="keyword_search",
            )
        return treated

    @staticmethod
    def _treat(payload: dict, method: str, variant: str, tool: str | None = None) -> dict:
        body = json.dumps(payload).encode()
        treated = mcp_telemetry_proxy.apply_treatment(
            body,
            "application/json",
            TestTreatmentVariants._context(method, tool),
            variant,
        )
        return json.loads(treated)

    @pytest.fixture(autouse=True)
    def _fresh_nudge(self):
        sourcegraph_server_treatment.reset_nudge_state()
        yield
        sourcegraph_server_treatment.reset_nudge_state()

    def test_instructions_variant_leaves_the_tool_list_alone(self) -> None:
        payload = TestServerTreatmentOverlay._tools_body()
        treated = self._treat(payload, "tools/list", "instructions")
        assert treated == payload

    def test_edges_variant_leaves_initialize_alone(self) -> None:
        treated = self._treat(
            {"jsonrpc": "2.0", "id": 7, "result": {}}, "initialize", "edges"
        )
        assert "instructions" not in treated["result"]

    def test_edges_variant_still_rewrites_the_tool_list(self) -> None:
        treated = self._treat(
            TestServerTreatmentOverlay._tools_body(), "tools/list", "edges"
        )
        assert "runs its own multi-round search" in treated["result"]["tools"][0][
            "description"
        ]

    def test_nudge_reaches_the_schema_carrying_result_the_model_reads(self) -> None:
        """A schema-carrying tool's structuredContent is what the client shows."""
        treated = self._search(sourcegraph_server_treatment.NUDGE_AFTER_CALLS)
        assert "code_finder runs this search loop" in treated["result"][
            "structuredContent"
        ]["text"]
        assert "code_finder runs this search loop" in treated["result"]["content"][0][
            "text"
        ]

    def test_a_result_with_no_text_raises_rather_than_running_as_control(self) -> None:
        self._search(sourcegraph_server_treatment.NUDGE_AFTER_CALLS - 1)
        with pytest.raises(sourcegraph_server_treatment.TreatmentError):
            self._treat(
                {"jsonrpc": "2.0", "id": 7, "result": {"content": []}},
                "tools/call",
                "nudge",
                tool="keyword_search",
            )

    def test_the_referral_waits_until_the_agent_is_hunting(self) -> None:
        """A first search on a literal the agent already knows is left alone."""
        early = self._search(sourcegraph_server_treatment.NUDGE_AFTER_CALLS - 1)
        assert early["result"]["structuredContent"]["text"] == "hits"
        assert "code_finder runs this search loop" in self._search()["result"][
            "structuredContent"
        ]["text"]

    def test_nudge_fires_once_per_process(self) -> None:
        after = sourcegraph_server_treatment.NUDGE_AFTER_CALLS
        assert "code_finder" in self._search(after)["result"]["content"][0]["text"]
        assert self._search()["result"]["content"][0]["text"] == "hits"

    def test_nudge_leaves_code_finder_results_alone(self) -> None:
        payload = {"jsonrpc": "2.0", "id": 7, "result": {"content": [{"text": "hits"}]}}
        self._search(sourcegraph_server_treatment.NUDGE_AFTER_CALLS - 1)
        assert self._treat(payload, "tools/call", "nudge", tool="code_finder") == payload

    def test_nudge_leaves_errors_alone(self) -> None:
        payload = {
            "jsonrpc": "2.0",
            "id": 7,
            "result": {"isError": True, "content": [{"text": "boom"}]},
        }
        self._search(sourcegraph_server_treatment.NUDGE_AFTER_CALLS - 1)
        assert (
            self._treat(payload, "tools/call", "nudge", tool="keyword_search") == payload
        )

    def test_nudge_variant_does_not_touch_the_tool_list(self) -> None:
        payload = TestServerTreatmentOverlay._tools_body()
        assert self._treat(payload, "tools/list", "nudge") == payload

    def test_an_unknown_variant_raises_rather_than_serving_control_text(self) -> None:
        with pytest.raises(sourcegraph_server_treatment.TreatmentError):
            self._treat(
                {"jsonrpc": "2.0", "id": 7, "result": {}}, "initialize", "not-a-variant"
            )


def test_forced_finder_allows_a_repeat_call_once_every_repo_is_covered(
    tmp_path: Path,
) -> None:
    """Asking one repo twice still keeps retrieval on the server side."""
    flask = "github.com/sg-evals/flask--2.3.3"
    werkzeug = "github.com/sg-evals/werkzeug--2.3.7"
    trace = tmp_path / "mcp_trace.jsonl"
    records = [
        _request(1, "code_finder", f"In {flask}, find context handling"),
        _response(1),
        _request(2, "code_finder", f"In {flask}, find more context handling"),
        _response(2),
        _request(3, "code_finder", f"In {werkzeug}, find the routing map"),
        _response(3),
    ]
    trace.write_text("\n".join(json.dumps(record) for record in records) + "\n")

    telemetry = build_retrieval_telemetry(
        trace,
        mode="mcp_code_finder",
        expected_repo_count=2,
        expected_repositories=[flask, werkzeug],
        outer_usage={},
    )

    assert telemetry["valid"] is True, telemetry["invalid_reason"]
    assert telemetry["code_finder_calls"] == 3


def test_forced_finder_still_rejects_a_missed_repo(tmp_path: Path) -> None:
    flask = "github.com/sg-evals/flask--2.3.3"
    werkzeug = "github.com/sg-evals/werkzeug--2.3.7"
    trace = tmp_path / "mcp_trace.jsonl"
    records = [
        _request(1, "code_finder", f"In {flask}, find context handling"),
        _response(1),
    ]
    trace.write_text("\n".join(json.dumps(record) for record in records) + "\n")

    telemetry = build_retrieval_telemetry(
        trace,
        mode="mcp_code_finder",
        expected_repo_count=2,
        expected_repositories=[flask, werkzeug],
        outer_usage={},
    )

    assert telemetry["valid"] is False
    assert "at least 2" in telemetry["invalid_reason"]
