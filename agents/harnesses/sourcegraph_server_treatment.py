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

# Which half of the branch an arm applies. The commit bundles two independent
# changes -- what the agent reads before its first call (`initialize`
# instructions) and what it reads in the tool list (the referral edges between
# tool descriptions) -- and the bundled arm cannot say which one moved
# adoption. `nudge` is not on the branch at all: it is a candidate change that
# acts after the agent has already chosen a direct search, which is the only
# point at which the server can observe how far the agent is from its answer.
VARIANT_BUNDLE = "bundle"
VARIANT_INSTRUCTIONS = "instructions"
VARIANT_EDGES = "edges"
VARIANT_NUDGE = "nudge"
VARIANTS = (VARIANT_BUNDLE, VARIANT_INSTRUCTIONS, VARIANT_EDGES, VARIANT_NUDGE)

# The methods each variant must see. A variant is only applied to responses it
# names, so an arm that rewrites nothing it was not asked to rewrite stays a
# clean single-factor manipulation.
VARIANT_METHODS: dict[str, frozenset[str]] = {
    VARIANT_BUNDLE: frozenset({"initialize", "tools/list"}),
    VARIANT_INSTRUCTIONS: frozenset({"initialize"}),
    VARIANT_EDGES: frozenset({"tools/list"}),
    VARIANT_NUDGE: frozenset({"tools/call"}),
}

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


# Appended once, to the NUDGE_AFTER_CALLS'th direct-search result of a session. The bundled
# branch acts before the agent has done anything, so it can only route on the
# shape of the prompt; this acts at the first moment the server knows the agent
# is searching rather than reading, and says so once rather than on every call.
NUDGE_TOOLS = frozenset({"keyword_search", "nls_search"})
# How many direct searches the agent makes before the referral is offered.
# Measured on the NATS incident task: the first search is a literal lifted out
# of the incident log, which the agent is right to run and right to decline a
# referral on. What costs is the searching that follows once that string has
# been confirmed, so the referral waits until the agent's own behaviour, not
# the shape of its prompt, says it is hunting rather than confirming.
NUDGE_AFTER_CALLS = 5
NUDGE_TEXT = (
    "\n\n[sourcegraph] You ran a direct search. If you are still working out"
    " which files are relevant rather than confirming something you already"
    " know the name of, code_finder runs this search loop for you against a"
    " named repository and returns the relevant paths and line ranges in one"
    " call. If you already know what you are looking for, ignore this."
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


# One nudge per proxy process, and the proxy runs one per container per run.
# Repeating it on every direct search would measure nagging rather than the
# referral, and would charge the agent for the same sentence a dozen times.
_nudged = False
_direct_searches = 0


def reset_nudge_state() -> None:
    """Forget the session's search count. For tests; each run gets a process."""
    global _nudged, _direct_searches
    _nudged = False
    _direct_searches = 0


def _append_nudge(result: dict[str, Any]) -> bool:
    """Append the referral everywhere the client may read the result from, once.

    A tool with an ``outputSchema`` returns both ``content`` text blocks and a
    ``structuredContent`` object, and the client decides which one reaches the
    model. Sourcegraph's search tools are schema-carrying, and appending to
    ``content`` alone was measured to be invisible: the agent saw the untreated
    result and the arm silently ran as its own control. So the referral goes
    into both, and a result offering neither raises.
    """
    global _nudged, _direct_searches
    _direct_searches += 1
    if _nudged or _direct_searches < NUDGE_AFTER_CALLS:
        return False
    appended = False
    content = result.get("content")
    if isinstance(content, list):
        for block in reversed(content):
            if isinstance(block, dict) and isinstance(block.get("text"), str):
                block["text"] = block["text"] + NUDGE_TEXT
                appended = True
                break
    structured = result.get("structuredContent")
    if isinstance(structured, dict) and isinstance(structured.get("text"), str):
        structured["text"] = structured["text"] + NUDGE_TEXT
        appended = True
    if not appended:
        raise TreatmentError("tools/call result carried no text to append to")
    _nudged = True
    return True


def applies_to(method: str, *, variant: str = VARIANT_BUNDLE) -> bool:
    """Whether this variant rewrites responses to ``method`` at all."""
    try:
        return method in VARIANT_METHODS[variant]
    except KeyError:
        raise TreatmentError(f"unknown treatment variant {variant!r}") from None


def apply(
    payload: dict[str, Any],
    *,
    method: str,
    tool: str | None = None,
    variant: str = VARIANT_BUNDLE,
) -> bool:
    """Apply one variant of the server change to one JSON-RPC response, in place.

    Returns True when the payload was rewritten. Raises TreatmentError when the
    payload is one this overlay must patch but cannot.
    """
    if not applies_to(method, variant=variant):
        return False
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
    if method == "tools/call":
        if tool not in NUDGE_TOOLS or result.get("isError"):
            return False
        return _append_nudge(result)
    return False


def provenance(*, variant: str = VARIANT_BUNDLE) -> dict[str, str]:
    """Describe what was applied, for the run's evidence trail."""
    return {
        "treatment_id": TREATMENT_ID,
        "treatment_variant": variant,
        "source_repo": SOURCE_REPO,
        "source_branch": SOURCE_BRANCH,
        "source_commit": SOURCE_COMMIT,
    }
