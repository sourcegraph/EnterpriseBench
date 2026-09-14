"""Sourcegraph MCP preamble builder for EnterpriseBench.

Provides system prompt templates and assembly for Sourcegraph MCP tool access
in benchmark task runs. Ported from CSB's sourcegraph.py with EnterpriseBench
enhancements (deepsearch tools, efficiency rules, if-stuck recovery).

Transport: HTTP (to Sourcegraph instance via /.api/mcp/all endpoint)
Code access: Remote-only (mcp_only) or hybrid (local + remote)
Auth: SOURCEGRAPH_ACCESS_TOKEN environment variable
"""

from __future__ import annotations

import logging
import sys
from pathlib import Path
from typing import Sequence

_REPO_ROOT = Path(__file__).resolve().parents[4]
sys.path.insert(0, str(_REPO_ROOT / "scripts" / "infra"))
from mirror_naming import derive_mirror_name  # noqa: E402

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Tool reference — describes all available Sourcegraph MCP tools
# ---------------------------------------------------------------------------

TOOL_REFERENCE = """\
# Sourcegraph MCP Tools

Available tools for searching the remote repository:

| Tool | Purpose |
|------|---------|
| `keyword_search` | Exact keyword/pattern search across files. Use `repo:^<repo>$` filter. |
| `nls_search` | Semantic/fuzzy search (broader matching, good for exploratory queries) |
| `read_file` | Read file contents from the indexed repository |
| `list_files` | List directory contents |
| `list_repos` | Search and list available repositories |
| `go_to_definition` | Jump to a symbol's definition (cross-repo support) |
| `find_references` | Find all usages of a symbol |
| `commit_search` | Search commit history by message, author, or content |
| `diff_search` | Search code changes (added/removed lines) |
| `compare_revisions` | Compare two branches/commits/tags |
| `deepsearch` | AI-powered deep analysis (async: returns a polling link) |
| `deepsearch_read` | Read Deep Search results (call 60+ seconds after deepsearch) |
| `code_finder` | Launch a bounded search agent that returns task-relevant code context |
| `evaluator` | Judge whether returned context answers the question |
| `get_contributor_repos` | List the repositories a contributor has worked in |

Note: Sourcegraph indexes the remote repository. Local source files are present \
in /workspace, but whether you may READ them depends on the access mode.
"""


# ---------------------------------------------------------------------------
# Preamble templates — mode-specific system prompt sections
# ---------------------------------------------------------------------------

_MCP_ONLY_HEADER = (
    "**IMPORTANT: The repositories exist in /workspace but you do not have "
    "permission to read them — every local read will fail with Permission "
    "denied. Do not try to work around this; it is enforced by the filesystem, "
    "not by instruction. You MUST use Sourcegraph MCP tools for all code "
    "access.**"
)

_HYBRID_HEADER = """\
# Sourcegraph MCP Tools Available

You have Sourcegraph MCP tools for **cross-repo code intelligence**. \
Use them for discovery and navigation — but read and edit local files directly.

## When to Use MCP vs Local Tools

| Task | Use MCP | Use Local |
|------|---------|----------|
| Find symbols across repos | `keyword_search` | — |
| Semantic/concept search | `nls_search` | — |
| Trace callers/references | `find_references` | — |
| Jump to definition | `go_to_definition` | — |
| Search commit history | `commit_search` | — |
| Read a file in /workspace | — | `Read` / `cat` |
| Search within local files | — | `Grep` / `grep` |
| Read a file NOT in /workspace | `read_file` | — |
| Deep code analysis | `deepsearch` + `deepsearch_read` | — |

**Key rule: never use `mcp__sourcegraph__read_file` for files that exist \
locally in /workspace.** Local reads are instant; MCP reads add network \
latency and waste turns.

## Workflow

1. **Discover with MCP** — find relevant files, symbols, and cross-repo dependencies
2. **Read locally** — use Read/cat for files in /workspace
3. **Navigate with MCP** — trace references and definitions across repo boundaries
4. **Edit locally** — modify /workspace files based on what you learned"""

_FORCED_FINDER_WORKFLOW = """\
## Required Code Finder Workflow

Use `code_finder` **exactly once per repository** listed below. Give each call \
a comprehensive description of the task, failure symptoms, and the evidence \
you need from that repository.

Do not call any direct Sourcegraph retrieval tool. This prohibition includes \
`keyword_search`, `nls_search`, `read_file`, `list_files`, `list_repos`, \
`go_to_definition`, `find_references`, `commit_search`, `diff_search`, \
`compare_revisions`, `deepsearch`, `deepsearch_read`, `evaluator`, and \
`get_contributor_repos`. The purpose of this \
arm is to measure Code Finder as the sole remote retrieval path.

You may use the shell only to reason over already-returned context and to write \
the required `/workspace/agent_output/answer.json`. Local repository source is \
filesystem-gated and must not be read."""

_ASSISTED_FINDER_WORKFLOW = """\
## Required Code Finder Bootstrap

Call `code_finder` at least once before using another Sourcegraph retrieval \
tool. Give it a comprehensive description of the task and failure symptoms. \
Use its result as the discovery plan, then make only targeted follow-up calls \
such as `read_file`, `go_to_definition`, `find_references`, or a narrowly \
scoped search when the returned context identifies a concrete gap.

Local repository source is filesystem-gated. Use Sourcegraph for code access \
and the shell only to write the required `/workspace/agent_output/answer.json`."""

_TOAST_HEADER = (
    "**IMPORTANT: The repositories exist in /workspace but you do not have "
    "permission to read them — every local read will fail with Permission "
    "denied. Do not try to work around this; it is enforced by the filesystem, "
    "not by instruction. Your only path to code is the `toast_search` tool.**"
)

_TOAST_WORKFLOW = """\
## Required Toast Search Workflow

`toast_search` is an agentic retrieval subagent: it runs its own multi-round \
search over the repositories you name and answers with the ranked files and \
snippets it found. Give it a comprehensive description of the task, the failure \
symptoms, and the evidence you need — not a bare keyword.

Call it as often as you need, one focused question per call, and pass `repos` \
using the identifiers listed above exactly as written. No other remote \
retrieval tool is available in this arm.

You may use the shell only to reason over already-returned context and to write \
the required `/workspace/agent_output/answer.json`. Local repository source is \
filesystem-gated and must not be read."""

_DIRECT_MCP_RULE = """\
## Direct MCP Arm

Do not call `code_finder`. This arm measures direct Sourcegraph retrieval with \
the search, navigation, and file-reading tools below; Code Finder is measured \
separately."""

_DEFAULT_MCP_RULE = """\
## Default MCP Arm

Every tool in the table above is available, `code_finder` included. Nothing \
here steers you toward or away from any of them: choose whatever retrieval \
path you judge best for this task, exactly as you would against a Sourcegraph \
MCP server you had not been given instructions about. This arm measures that \
unprompted choice."""


_TOOL_SELECTION = """\
## Tool Selection

**Decision logic:**
1. Know the exact symbol? -> `keyword_search`
2. Know the concept, not the name? -> `nls_search`
3. Need definition of a symbol? -> `go_to_definition`
4. Need all callers/references? -> `find_references`
5. Need full file content? -> `read_file`
6. Need deep cross-repo analysis? -> `deepsearch` (then `deepsearch_read` after 60s)"""

_SCOPING_RULES = """\
## Scoping (Always Do This)

```
repo:^github.com/ORG/REPO$           # Exact repo (preferred)
repo:github.com/ORG/                 # All repos in org
file:.*\\.ts$                         # TypeScript only
file:src/api/                        # Specific directory
```

Start narrow. Expand only if results are empty."""

_EFFICIENCY_RULES = """\
## Efficiency Rules

- Chain searches logically: search -> read -> references -> definition
- Don't re-search for the same pattern; use results from prior calls
- Prefer `keyword_search` over `nls_search` when you have exact terms
- Read 2-3 related files before synthesising, rather than one at a time
- Don't read 20+ remote files without writing code — once you understand the pattern, start implementing
- Use `deepsearch` only for complex cross-repo questions that simpler tools can't answer
- After calling `deepsearch`, wait at least 60 seconds before calling `deepsearch_read`
- Batch related searches together rather than making one-at-a-time calls"""

_IF_STUCK = """\
## If Stuck

If MCP search returns no results:
1. Broaden the search query (synonyms, partial identifiers)
2. Try `nls_search` for semantic matching
3. Use `list_files` to browse the directory structure
4. Use `list_repos` to verify the repository name
5. Try `deepsearch` for AI-powered deep analysis of the question
6. Check if the symbol exists under a different module or package name"""


# ---------------------------------------------------------------------------
# Repo scope builder
# ---------------------------------------------------------------------------


# Arms that leave the choice of tool to the agent: the server as shipped, and
# every arm that varies only what the server itself says.
_DEFAULT_PREAMBLE_MODES = frozenset(
    {
        "mcp_default",
        "mcp_treatment",
        "mcp_treat_instr",
        "mcp_treat_edges",
        "mcp_treat_nudge",
    }
)


def _build_repo_scope(repos: Sequence[dict]) -> str:
    """Build Sourcegraph repo scoping section from task repos.

    Maps each task repo (e.g. github.com/grpc/grpc-go@v1.4.0) to its
    sg-evals mirror name (e.g. sg-evals/grpc-go--v1.4.0) for accurate
    MCP search scoping.
    """
    if not repos:
        return ""

    lines = ["## Sourcegraph Repository Scoping\n"]
    lines.append(
        "These repos are indexed on Sourcegraph under `sg-evals/` mirrors. "
        "**Always scope your MCP searches to these repos:**\n"
    )

    for repo in repos:
        url = repo.get("url", "")
        rev = repo.get("rev", "")
        path = repo.get("path", "")
        if not url:
            continue

        # Extract org/repo from URL
        org_repo = (
            url.replace("https://github.com/", "")
            .replace("http://github.com/", "")
            .rstrip("/")
        )
        if org_repo.endswith(".git"):
            org_repo = org_repo[:-4]
        repo_name = org_repo.split("/")[-1]

        sg_mirror = derive_mirror_name(url, rev)

        lines.append(f"- **{path or repo_name}** (local: `/workspace/{path}/`)")
        lines.append(f"  - MCP filter: `repo:^github.com/{sg_mirror}$`")
        lines.append(f"  - Upstream: `{org_repo}@{rev}`")

    lines.append("")
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def _build_toast_repo_scope(repos: Sequence[dict]) -> str:
    """Repo identifiers in the shape ``toast_search``'s ``repos`` field takes.

    The Sourcegraph preamble renders scoping as ``repo:^...$`` filter syntax,
    which is a search-query fragment. Toast takes plain store identifiers, so
    handing it the filter form would send the regex anchors through as part of
    the repo name.
    """
    if not repos:
        return ""

    lines = ["## Repositories to Search\n"]
    lines.append(
        "Pass these identifiers verbatim as the `repos` argument to "
        "`toast_search`:\n"
    )
    for repo in repos:
        url = repo.get("url", "")
        rev = repo.get("rev", "")
        if not url:
            continue
        path = repo.get("path", "") or url.rstrip("/").split("/")[-1]
        lines.append(f"- `github.com/{derive_mirror_name(url, rev)}` — {path}")
    lines.append("")
    return "\n".join(lines)


def build_system_prompt(
    mode: str,
    repos: Sequence[dict] | None = None,
) -> str:
    """Assemble the full MCP preamble for a given tool-access mode.

    Args:
        mode: One of "mcp_only", "mcp_default", "mcp_treatment", "hybrid",
            "mcp_code_finder",
            "mcp_assisted", or "mcp_toast". Returns empty string for
            "baseline".
        repos: List of repo dicts from task.toml (each with url, rev, path keys).

    Returns:
        The assembled preamble string, or empty string for baseline mode.
    """
    if mode == "baseline":
        return ""

    parts: list[str] = []

    if mode == "mcp_toast":
        parts.append(_TOAST_HEADER)
        toast_scope = _build_toast_repo_scope(repos or [])
        if toast_scope:
            parts.append(toast_scope)
        parts.append(_TOAST_WORKFLOW)
        return "\n\n".join(parts)

    if mode in {
        "mcp_only",
        "mcp_default",
        "mcp_treatment",
        "mcp_treat_instr",
        "mcp_treat_edges",
        "mcp_treat_nudge",
        "mcp_code_finder",
        "mcp_assisted",
    }:
        parts.append(_MCP_ONLY_HEADER)
    elif mode == "hybrid":
        parts.append(_HYBRID_HEADER)
    else:
        logger.warning("Unknown mode %r; returning empty preamble", mode)
        return ""

    # Repo scoping (dynamic, from task metadata)
    repo_scope = _build_repo_scope(repos or [])
    if repo_scope:
        parts.append(repo_scope)

    if mode == "mcp_code_finder":
        parts.append(_FORCED_FINDER_WORKFLOW)
        return "\n\n".join(parts)
    if mode == "mcp_assisted":
        parts.append(_ASSISTED_FINDER_WORKFLOW)
    elif mode == "mcp_only":
        parts.append(_DIRECT_MCP_RULE)
    elif mode in _DEFAULT_PREAMBLE_MODES:
        # No directive either way. The three Finder arms all tell the agent
        # what to do with code_finder (forbid, bootstrap, or force one call per
        # repo), which measures the tool rather than the server as shipped.
        # This arm leaves the choice to the agent so the result answers "what
        # happens when a customer points an agent at the default endpoint".
        # mcp_treatment shares this preamble exactly: it varies only the
        # server's own tool descriptions and initialize instructions, so any
        # difference between the two arms is attributable to the server change.
        parts.append(_DEFAULT_MCP_RULE)

    # Common sections for both modes
    parts.append(_TOOL_SELECTION)
    parts.append(_SCOPING_RULES)
    parts.append(_EFFICIENCY_RULES)
    parts.append(_IF_STUCK)

    return "\n\n".join(parts)
