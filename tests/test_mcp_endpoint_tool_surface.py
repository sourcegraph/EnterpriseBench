"""The documented tool table must match what the MCP server actually serves.

Marked ``network`` and excluded from the default suite. It exists because the
drift is silent and was real: the endpoint comment in run_task.py claimed "13
tools vs 8 on base endpoint" while both endpoints served 15, and `evaluator`
and `get_contributor_repos` were served for some time without appearing in
TOOL_REFERENCE. The mcp_default arm is defined as "the server as shipped", so
a preamble that under-reports the surface misdescribes that arm.

The prefix assertion is the other half: sg_cli.py hardcodes v1's ``sg_``-
prefixed names while every MCP preamble and telemetry breakdown uses the
un-prefixed ones. If the two endpoints ever stop being the same tools under
two names, the cli arm and the MCP arms stop being comparable.
"""

from __future__ import annotations

import json
import os
import sys
import urllib.request
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from agents.harnesses import sourcegraph_server_treatment  # noqa: E402
from agents.harnesses.claude.mcp.sourcegraph import TOOL_REFERENCE  # noqa: E402

pytestmark = pytest.mark.network

# Cloudflare rejects urllib's default User-Agent with a 1010 fingerprint block,
# which surfaces as a 403 and reads exactly like an expired token.
_UA = (
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/128.0 Safari/537.36"
)
_BASE = "https://demo.sourcegraph.com/.api/mcp"


def _rpc(url: str, body: dict, token: str, session: str | None = None):
    headers = {
        "Authorization": f"token {token}",
        "Content-Type": "application/json",
        "Accept": "application/json, text/event-stream",
        "User-Agent": _UA,
    }
    if session:
        headers["Mcp-Session-Id"] = session
    request = urllib.request.Request(url, json.dumps(body).encode(), headers)
    response = urllib.request.urlopen(request, timeout=30)
    return response, response.read().decode()


def _served_tools(url: str, token: str) -> set[str]:
    return {tool["name"] for tool in _served_tool_objects(url, token)}


def _served_tool_objects(url: str, token: str) -> list[dict]:
    response, _ = _rpc(
        url,
        {
            "jsonrpc": "2.0",
            "id": 1,
            "method": "initialize",
            "params": {
                "protocolVersion": "2025-06-18",
                "capabilities": {},
                "clientInfo": {"name": "eb-tests", "version": "1"},
            },
        },
        token,
    )
    session = response.headers.get("Mcp-Session-Id")
    try:
        _rpc(url, {"jsonrpc": "2.0", "method": "notifications/initialized"}, token, session)
    except Exception:  # the notification is fire-and-forget; a refusal is not fatal
        pass
    _, body = _rpc(
        url, {"jsonrpc": "2.0", "id": 2, "method": "tools/list", "params": {}}, token, session
    )
    for line in body.splitlines():  # the server may answer as SSE
        if line.startswith("data: "):
            body = line[len("data: ") :]
    return json.loads(body)["result"]["tools"]


@pytest.fixture(scope="module")
def token() -> str:
    value = os.environ.get("SOURCEGRAPH_ACCESS_TOKEN")
    if not value:
        pytest.skip("SOURCEGRAPH_ACCESS_TOKEN is not set")
    return value


def test_tool_reference_documents_every_served_tool(token: str) -> None:
    served = _served_tools(f"{_BASE}/all", token)

    undocumented = sorted(name for name in served if f"`{name}`" not in TOOL_REFERENCE)
    assert not undocumented, (
        f"the MCP server serves {undocumented} but TOOL_REFERENCE does not list "
        "them; the mcp_default arm claims to present the server as shipped"
    )


def test_both_endpoints_serve_the_same_tools_under_two_names(token: str) -> None:
    unprefixed = _served_tools(f"{_BASE}/all", token)
    prefixed = _served_tools(f"{_BASE}/v1", token)

    assert {f"sg_{name}" for name in unprefixed} == prefixed


def test_code_finder_is_on_the_default_endpoint(token: str) -> None:
    """The premise of the mcp_default arm, asserted rather than assumed."""
    assert "sg_code_finder" in _served_tools(f"{_BASE}/v1", token)


def test_treatment_anchors_still_match_what_the_server_serves(token: str) -> None:
    """The mcp_treatment overlay patches live text; drift makes the arm unrunnable.

    The overlay raises rather than serving control descriptions, so drift would
    surface as a failed run rather than a wrong number. This test turns that
    into a cheap failure here instead of an expensive one mid-sweep.
    """
    served = {
        tool["name"]: tool["description"]
        for tool in _served_tool_objects(f"{_BASE}/all", token)
    }
    for name, anchor, _replacement in sourcegraph_server_treatment.DESCRIPTION_REWRITES:
        assert name in served, f"{name} is no longer served"
        assert anchor in served[name], (
            f"{name}'s description no longer contains the text the treatment "
            "overlay anchors on"
        )
