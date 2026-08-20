#!/usr/bin/env python3
"""Client-side overlay of an unshipped Sourcegraph server change.

The `mcp_treatment` arm measures a change to Sourcegraph's MCP server that we
do not operate: the server is hosted, and only Sourcegraph can deploy to it.
So the arm applies the change where we do have control, in the local telemetry
proxy, by rewriting the `initialize` and `tools/list` responses on their way to
the agent. What the agent sees is byte-identical to what the patched server
would have sent; nothing else about the arm differs from `mcp_default`.

The strings below are copied from the sourcegraph branch named in
`SOURCE_BRANCH` at `SOURCE_COMMIT`. Each rewrite is anchored on the text the
live server currently serves. An anchor that no longer matches means the server
moved and this overlay no longer reproduces the branch, so `apply` raises rather
than passing the response through: an arm that silently degrades into its own
control would report a null result as a real one.
"""

from __future__ import annotations

from typing import Any

TREATMENT_ID = "codefinder-discoverability"
SOURCE_REPO = "sourcegraph/sourcegraph"
SOURCE_BRANCH = "codefinder-discoverability"
SOURCE_COMMIT = "2bdad35ca828023531b51ba0a1e69b6831244955"

# cmd/frontend/internal/mcpapi/server.go: ServerOptions.Instructions
INSTRUCTIONS = """\
These tools search and read code that Sourcegraph has indexed.

Start with code_finder when you know the repository but not yet which files
matter: describe the task and it runs its own multi-round search, returning the
relevant file paths and line ranges in one call. Use the direct tools --
keyword_search for an exact string, nls_search for an uncertain one,
go_to_definition and find_references to follow a symbol, read_file for a path
you already have -- when you know specifically what you are looking for.

Reaching for the direct tools first and iterating toward the right files works,
but costs several times more tokens and wall-clock than delegating that search
once."""

# (tool name, anchor served today, replacement from the branch)
DESCRIPTION_REWRITES: tuple[tuple[str, str, str], ...] = (
    (
        "code_finder",
        "A fast file-finding agent for a known repository.\n"
        "The tool returns relevant file paths and line ranges with context"
        " about why each file is relevant.",
        "A search agent that finds the code relevant to a task in a known"
        " repository, in one call.\n"
        "Give it an engineering request and it runs its own multi-round search,"
        " returning the relevant file paths and line ranges with context about"
        " why each file is relevant.\n"
        "Prefer it over issuing your own sequence of searches and reads when you"
        " do not yet know which files matter; reach for the direct search tools"
        " when you already know the exact string, symbol, or path you are"
        " looking for.",
    ),
    (
        "read_file",
        "- ALWAYS verify both the repository and file exist by using"
        " list_repos, list_files, keyword_search, or nls_search first",
        "- ALWAYS verify both the repository and file exist by using"
        " list_repos, list_files, code_finder, keyword_search, or nls_search"
        " first\n"
        "- If you do not yet know which files are relevant, code_finder locates"
        " them in one call and returns the paths and line ranges to read",
    ),
    (
        "keyword_search",
        "Important: In these cases, use the nls_search tool instead.",
        "Important: In these cases, use the nls_search tool instead.\n"
        "- When you do not know which files are relevant yet and would otherwise"
        " search, read, and search again to find them. Use code_finder instead:"
        " it runs that loop internally against a named repository and returns"
        " the relevant file paths and line ranges in one call.",
    ),
)


class TreatmentError(ValueError):
    """The overlay could not be applied exactly as the branch defines it.

    ValueError, so the proxy's forwarding path turns it into a clean 502 the
    run can attribute, rather than a dropped connection.
    """


def _rewrite_tools(tools: list[Any]) -> int:
    by_name = {
        tool.get("name"): tool
        for tool in tools
        if isinstance(tool, dict) and isinstance(tool.get("name"), str)
    }
    applied = 0
    for name, anchor, replacement in DESCRIPTION_REWRITES:
        tool = by_name.get(name)
        if tool is None:
            raise TreatmentError(f"tools/list did not offer {name!r}")
        description = tool.get("description")
        if not isinstance(description, str):
            raise TreatmentError(f"{name!r} has no string description")
        if anchor not in description:
            raise TreatmentError(
                f"{name!r} no longer serves the anchored text this overlay patches"
            )
        tool["description"] = description.replace(anchor, replacement, 1)
        applied += 1
    return applied


def apply(payload: dict[str, Any], *, method: str) -> bool:
    """Apply the branch's server change to one JSON-RPC response, in place.

    Returns True when the payload was rewritten. Raises TreatmentError when the
    payload is one this overlay must patch but cannot.
    """
    result = payload.get("result")
    if not isinstance(result, dict):
        return False
    if method == "initialize":
        result["instructions"] = INSTRUCTIONS
        return True
    if method == "tools/list":
        tools = result.get("tools")
        if not isinstance(tools, list):
            raise TreatmentError("tools/list result carried no tool list")
        return _rewrite_tools(tools) > 0
    return False


def provenance() -> dict[str, str]:
    """Describe what was applied, for the run's evidence trail."""
    return {
        "treatment_id": TREATMENT_ID,
        "source_repo": SOURCE_REPO,
        "source_branch": SOURCE_BRANCH,
        "source_commit": SOURCE_COMMIT,
    }
