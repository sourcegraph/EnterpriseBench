"""Expose Toast-1 to a containerized Claude Code agent as an MCP tool.

The four-arm sweep in this directory compares *retrieval engines* head to
head: Toast-1's own loop against `code_finder`, each handed the task prompt
and scored on what it returned. That is not the product question. Sourcegraph
would ship Toast-1 as a tool their MCP server offers, and a coding agent would
decide when to call it and what to do with the answer. Measuring that needs
Toast-1 inside the agent's tool list, not beside it.

Topology: this server runs on the *host* and the benchmark container reaches
it over the docker bridge gateway (172.17.0.1 by default). Toast-1 stays out
of the task image, which matters for three reasons -- `agent_harness` is a
third-party async package that `_install_sgx`'s stdlib-only staging cannot
handle, the embedding and generation credentials never enter a container the
agent controls, and the 141 task images stay byte-identical across arms, which
is the property `mode_gate.py` exists to protect.

It also happens to be the faithful topology. A hosted Toast-1 is what the
product decision is actually about.

One tool, `toast_search`. The agent passes a query and the repo scope from its
preamble; the server runs Toast-1's fixed four-round loop against Sourcegraph
and returns the ranked files with snippets. Every call is appended to a JSONL
trace so a credited path can be traced back to the search that found it --
the gap filed as EnterpriseBench-h4kba for the policy-level runners.

Usage:
    SOURCEGRAPH_ACCESS_TOKEN=... MXBAI_API_KEY=... \
        python toast_mcp_server.py --port 8770 --trace toast_calls.jsonl
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

from graphql import SourcegraphConfig  # noqa: E402
from sourcegraph_client import SourcegraphRetrievalClient  # noqa: E402
from toast_generate import build_generate_fn  # noqa: E402

from agent_harness import aio  # noqa: E402
from agent_harness.llm import sync_generation_as_async  # noqa: E402

PROTOCOL_VERSION = "2025-06-18"
SERVER_NAME = "toast"
# Bodies larger than this are a client bug, not a query. Same posture as the
# telemetry proxy's request cap.
MAX_BODY_BYTES = 2 * 1024 * 1024

TOOL_SCHEMA = {
    "name": "toast_search",
    "description": (
        "Agentic code search across the repositories in scope. Give it a "
        "natural-language description of what you are looking for -- a "
        "behavior, a symptom, a code path -- and it runs a multi-round "
        "search and returns the most relevant files with snippets. Prefer "
        "one well-described query over several keyword probes."
    ),
    "inputSchema": {
        "type": "object",
        "properties": {
            "query": {
                "type": "string",
                "description": "What you are looking for, in plain language.",
            },
            "repos": {
                "type": "array",
                "items": {"type": "string"},
                "description": (
                    "Repositories to search, exactly as named in your "
                    "instructions (for example github.com/sg-evals/opa--v0.69.0)."
                ),
            },
        },
        "required": ["query", "repos"],
    },
}


def token_counting_mode() -> str:
    """Resolve the tokenizer up front and report which mode the run will use.

    agent_harness budgets every prompt through its token counter and by
    default refuses to start without an exact one, which otherwise raises
    deep inside a rollout after the Sourcegraph work is already paid for.
    Resolving it at startup fails fast, and stamping the mode on every trace
    record keeps the token counts from being read as exact when they came
    from the chars/4 estimate. Same seam as run_toast.py.
    """
    from agent_harness import config as harness_config
    from agent_harness.config import searcher_agent_config
    from agent_harness.token_counter import ensure_rollout_token_counter

    ensure_rollout_token_counter(searcher_agent_config().get("model"))
    return "exact" if harness_config.TOKEN_COUNTER is not None else "chars_per_4_estimate"


class ToastBackend:
    """Runs Toast-1 searches. Owns the one event loop the harness needs."""

    def __init__(self, trace_path: Path | None) -> None:
        config = SourcegraphConfig.from_env()
        if not config.token:
            raise SystemExit("SOURCEGRAPH_ACCESS_TOKEN is not set")
        self._client = SourcegraphRetrievalClient(config)
        self._usage: list[dict] = []
        self._generate = sync_generation_as_async(
            build_generate_fn(usage_sink=self._usage)
        )
        self._token_counting = token_counting_mode()
        self._trace_path = trace_path
        self._trace_lock = threading.Lock()
        self._loop = asyncio.new_event_loop()
        threading.Thread(target=self._loop.run_forever, daemon=True).start()

    def search(self, query: str, repos: list[str]) -> dict:
        started = time.monotonic()
        future = asyncio.run_coroutine_threadsafe(
            aio.run_fast_agentic_search(
                query,
                store_identifiers=repos,
                client=self._client,
                generation_fn=self._generate,
            ),
            self._loop,
        )
        result = future.result()
        elapsed = round(time.monotonic() - started, 1)

        files: list[dict] = []
        for chunk in result.chunks:
            metadata = chunk.get("metadata") or {}
            path = metadata.get("path") or chunk.get("filename")
            if not path:
                continue
            files.append({"path": path, "repo": metadata.get("repo"), "text": chunk.get("text", "")})

        record = {
            "query": query,
            "repos": repos,
            # One entry per returned chunk, so a file that matched twice appears
            # twice; the scorer dedupes, the trace stays faithful to the rollout.
            "chunk_paths": [f["path"] for f in files],
            "rounds_executed": result.rounds_executed,
            "input_tokens": result.input_tokens,
            "output_tokens": result.output_tokens,
            "elapsed_seconds": elapsed,
            "token_counting": self._token_counting,
        }
        self._append_trace(record)
        return {"files": files, "meta": record}

    def _append_trace(self, record: dict) -> None:
        if self._trace_path is None:
            return
        with self._trace_lock:
            with self._trace_path.open("a") as handle:
                handle.write(json.dumps(record) + "\n")


def render_result(payload: dict) -> str:
    """Toast's ranked files as text, with the paths the agent can cite."""
    files = payload["files"]
    if not files:
        return "No results."
    blocks = []
    for entry in files:
        header = entry["path"] if not entry.get("repo") else f"{entry['repo']}/-/blob/{entry['path']}"
        body = entry["text"].strip()
        blocks.append(f"## {header}\n\n{body}" if body else f"## {header}")
    return "\n\n".join(blocks)


def dispatch(backend: ToastBackend, message: dict) -> dict | None:
    """One JSON-RPC message in, one response out (None for notifications)."""
    method = message.get("method")
    request_id = message.get("id")

    if method == "initialize":
        result = {
            "protocolVersion": PROTOCOL_VERSION,
            "capabilities": {"tools": {}},
            "serverInfo": {"name": SERVER_NAME, "version": "0.1.0"},
        }
    elif method == "tools/list":
        result = {"tools": [TOOL_SCHEMA]}
    elif method == "tools/call":
        params = message.get("params") or {}
        if params.get("name") != TOOL_SCHEMA["name"]:
            return _error(request_id, -32602, f"unknown tool: {params.get('name')}")
        arguments = params.get("arguments") or {}
        query = arguments.get("query")
        repos = arguments.get("repos")
        if not isinstance(query, str) or not query.strip():
            return _error(request_id, -32602, "query must be a non-empty string")
        if not isinstance(repos, list) or not all(isinstance(r, str) for r in repos) or not repos:
            return _error(request_id, -32602, "repos must be a non-empty list of strings")
        try:
            payload = backend.search(query, repos)
        except Exception as exc:  # a failed search is a tool error, not a crash
            return {
                "jsonrpc": "2.0",
                "id": request_id,
                "result": {
                    "content": [{"type": "text", "text": f"{type(exc).__name__}: {exc}"}],
                    "isError": True,
                },
            }
        result = {
            "content": [{"type": "text", "text": render_result(payload)}],
            "_meta": payload["meta"],
        }
    elif request_id is None:
        # A notification (notifications/initialized and friends) gets no reply.
        return None
    else:
        return _error(request_id, -32601, f"unknown method: {method}")

    return {"jsonrpc": "2.0", "id": request_id, "result": result}


def _error(request_id, code: int, message: str) -> dict:
    return {"jsonrpc": "2.0", "id": request_id, "error": {"code": code, "message": message}}


def build_handler(backend: ToastBackend):
    class Handler(BaseHTTPRequestHandler):
        protocol_version = "HTTP/1.1"

        def do_GET(self) -> None:  # noqa: N802 - BaseHTTPRequestHandler API
            if self.path.rstrip("/") == "/healthz":
                self._respond(200, b'{"status":"ok"}')
            else:
                self._respond(405, b'{"error":"POST only"}')

        def do_POST(self) -> None:  # noqa: N802 - BaseHTTPRequestHandler API
            length = int(self.headers.get("Content-Length") or 0)
            if length > MAX_BODY_BYTES:
                self._respond(413, b'{"error":"request too large"}')
                return
            raw = self.rfile.read(length)
            try:
                message = json.loads(raw)
            except ValueError:
                self._respond(400, json.dumps(_error(None, -32700, "parse error")).encode())
                return

            batch = message if isinstance(message, list) else [message]
            responses = [r for r in (dispatch(backend, m) for m in batch) if r is not None]
            if not responses:
                self._respond(202, b"")
                return
            body = json.dumps(responses if isinstance(message, list) else responses[0]).encode()
            self._respond(200, body)

        def _respond(self, status: int, body: bytes) -> None:
            self.send_response(status)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            if body:
                self.wfile.write(body)

        def log_message(self, fmt: str, *args) -> None:
            sys.stderr.write("%s - %s\n" % (self.address_string(), fmt % args))

    return Handler


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--host",
        default="172.17.0.1",
        help="bind address; defaults to the docker bridge gateway rather than "
        "0.0.0.0 because this server carries no auth and holds live "
        "Sourcegraph and generation credentials -- only the benchmark "
        "containers need to reach it",
    )
    parser.add_argument("--port", type=int, default=8770)
    parser.add_argument(
        "--trace",
        type=Path,
        default=None,
        help="append one JSON record per search, so a credited path can be "
        "traced to the query that found it",
    )
    args = parser.parse_args()

    # build_generate_fn resolves the Mixedbread key across its three accepted
    # env var names and raises loudly if none is set; no check is needed here.
    backend = ToastBackend(args.trace)
    server = ThreadingHTTPServer((args.host, args.port), build_handler(backend))
    print(f"toast MCP server on http://{args.host}:{args.port}/mcp", file=sys.stderr)
    server.serve_forever()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
