"""The condition's tool surface, served to Claude Code over stdio MCP.

The OpenRouter seam hands the model a tool list per request. Claude Code has no
such argument: its tool surface is whatever its built-ins plus its MCP servers
provide. So a condition becomes one MCP server exposing exactly that
condition's tools, and ``allowlist_hook.py`` denies everything else. Both
halves are needed. The server alone would leave Read/Grep/Bash in reach, and
the corpus clones are readable on local disk, so the lexical primitive would
leak into every arm and the independent variable would be gone.

Stdlib-only JSON-RPC over stdin/stdout, in the spirit of ``sg_cli``: this
process is spawned per run by a CLI that owns its lifecycle, and a dependency
here would be one more thing to install into every environment that replays a
run.

Instrumentation stays ours. Claude Code's own trace records tool calls, but
each seam must produce the same report fields or the two are not comparable, so
per-call latency, result size and hit paths come from ``Instrumented`` exactly
as they do on the OpenRouter path. The trace is kept as well, and the two are
cross-checked in ``run_claude_conditions``.

Configured entirely by environment, because the MCP config file that spawns it
is written per run:

    EB_CONDITION    condition name (selects the tool surface)
    EB_STORES       comma-separated store identifiers in scope
    EB_RUNDIR       directory to write submission.json and tool_log.json into
"""

from __future__ import annotations

import asyncio
import json
import os
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent))
sys.path.insert(0, str(HERE.parent.parent / "toast_retrieval"))

from backends import Instrumented  # noqa: E402
from conditions import CONDITIONS  # noqa: E402
from run_claude_policy import _dispatch, _submitted_files  # noqa: E402

PROTOCOL_VERSION = "2024-11-05"
SERVER_NAME = "retrieval"


def tool_schemas(condition_name: str) -> list[dict]:
    """The condition's tools in MCP shape, descriptions carried over verbatim.

    Rewording for MCP would silently reintroduce the coaching that
    ``check_description_parity`` exists to forbid, so the OpenAI-shaped
    description and parameter schema are passed through untouched and only the
    envelope changes.
    """
    schemas = []
    for tool in CONDITIONS[condition_name].tools:
        function = tool["function"]
        schemas.append(
            {
                "name": function["name"],
                "description": function["description"],
                "inputSchema": function["parameters"],
            }
        )
    return schemas


class Session:
    """One run's tool surface, submission and call log."""

    def __init__(self, condition_name: str, stores: tuple[str, ...], rundir: Path):
        self.condition_name = condition_name
        self.stores = stores
        self.rundir = rundir
        self.recorder = Instrumented(CONDITIONS[condition_name].backend())
        self.submitted: list[str] = []
        self.loop = asyncio.new_event_loop()

    def call(self, name: str, arguments: dict) -> tuple[str, bool]:
        if name == "submit_files":
            self.submitted = _submitted_files(arguments)
            self._persist()
            return (
                json.dumps({"submitted": len(self.submitted)}),
                False,
            )
        text = self.loop.run_until_complete(
            _dispatch(self.recorder, self.stores, name, arguments)
        )
        self._persist()
        return text, False

    def _persist(self) -> None:
        """Write after every call, not at exit.

        Claude Code owns this process's lifetime and may kill it on timeout or
        on its own turn limit. State that only exists at a clean shutdown is
        state that goes missing exactly on the runs worth inspecting.
        """
        self.rundir.mkdir(parents=True, exist_ok=True)
        (self.rundir / "submission.json").write_text(
            json.dumps({"retrieved_paths": self.submitted}, indent=2) + "\n"
        )
        (self.rundir / "tool_log.json").write_text(
            json.dumps(
                {
                    "condition": self.condition_name,
                    "tool_log": self.recorder.to_log(),
                    "tool_seconds": round(
                        sum(c.seconds for c in self.recorder.calls), 3
                    ),
                    "retrieval_result_chars": sum(
                        c.result_chars for c in self.recorder.calls
                    ),
                },
                indent=2,
            )
            + "\n"
        )


def handle(session: Session, request: dict) -> dict | None:
    """One JSON-RPC request in, one response out, or None for a notification."""
    method = request.get("method")
    request_id = request.get("id")
    if request_id is None:
        return None  # notification; nothing to answer

    def ok(result: dict) -> dict:
        return {"jsonrpc": "2.0", "id": request_id, "result": result}

    if method == "initialize":
        return ok(
            {
                "protocolVersion": PROTOCOL_VERSION,
                "capabilities": {"tools": {}},
                "serverInfo": {"name": SERVER_NAME, "version": "1.0.0"},
            }
        )
    if method == "ping":
        return ok({})
    if method == "tools/list":
        return ok({"tools": tool_schemas(session.condition_name)})
    if method == "tools/call":
        params = request.get("params") or {}
        name = params.get("name", "")
        arguments = params.get("arguments") or {}
        if name not in {t["name"] for t in tool_schemas(session.condition_name)}:
            # An out-of-condition tool name is a bug in the wiring, not a
            # retrieval result. It is answered as a tool error so the run keeps
            # going and the trace records it, and asserted over afterwards.
            return ok(
                {
                    "content": [
                        {"type": "text", "text": f"tool {name!r} is not in this condition"}
                    ],
                    "isError": True,
                }
            )
        text, is_error = session.call(name, arguments)
        return ok({"content": [{"type": "text", "text": text}], "isError": is_error})

    return {
        "jsonrpc": "2.0",
        "id": request_id,
        "error": {"code": -32601, "message": f"unknown method {method!r}"},
    }


def main() -> int:
    condition_name = os.environ.get("EB_CONDITION", "")
    if condition_name not in CONDITIONS:
        print(f"EB_CONDITION={condition_name!r} is not a condition", file=sys.stderr)
        return 2
    stores = tuple(s for s in os.environ.get("EB_STORES", "").split(",") if s)
    if not stores:
        print("EB_STORES is empty", file=sys.stderr)
        return 2
    rundir = Path(os.environ.get("EB_RUNDIR", ""))
    if not str(rundir):
        print("EB_RUNDIR is empty", file=sys.stderr)
        return 2

    session = Session(condition_name, stores, rundir)
    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue
        try:
            request = json.loads(line)
        except json.JSONDecodeError as exc:
            print(f"malformed request: {exc}", file=sys.stderr)
            continue
        response = handle(session, request)
        if response is not None:
            sys.stdout.write(json.dumps(response) + "\n")
            sys.stdout.flush()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
