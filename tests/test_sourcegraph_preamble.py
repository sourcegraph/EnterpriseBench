"""Tests for agents.harnesses.claude.mcp.sourcegraph preamble builder.

Verifies:
  - build_system_prompt() returns correct mode-specific headers
  - Repo scoping is included when repos are provided
  - Efficiency rules, tool selection, scoping, and if-stuck sections are present
  - deepsearch/deepsearch_read tools are documented
  - Baseline mode returns empty string
  - Unknown mode returns empty string
  - TOOL_REFERENCE constant is well-formed
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

# Make the agents package importable
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from agents.harnesses.claude.mcp.sourcegraph import (
    TOOL_REFERENCE,
    _build_repo_scope,
    build_system_prompt,
)

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

SAMPLE_REPOS = [
    {
        "url": "https://github.com/grpc/grpc-go",
        "rev": "v1.4.0",
        "path": "grpc-go",
    },
    {
        "url": "https://github.com/kubernetes/kubernetes",
        "rev": "abc123ef",
        "path": "k8s",
    },
]


# ---------------------------------------------------------------------------
# TOOL_REFERENCE constant
# ---------------------------------------------------------------------------


class TestToolReference:
    def test_contains_keyword_search(self) -> None:
        assert "keyword_search" in TOOL_REFERENCE

    def test_contains_nls_search(self) -> None:
        assert "nls_search" in TOOL_REFERENCE

    def test_contains_read_file(self) -> None:
        assert "read_file" in TOOL_REFERENCE

    def test_contains_deepsearch(self) -> None:
        assert "deepsearch" in TOOL_REFERENCE

    def test_contains_deepsearch_read(self) -> None:
        assert "deepsearch_read" in TOOL_REFERENCE

    def test_contains_go_to_definition(self) -> None:
        assert "go_to_definition" in TOOL_REFERENCE

    def test_contains_find_references(self) -> None:
        assert "find_references" in TOOL_REFERENCE


# ---------------------------------------------------------------------------
# Baseline mode
# ---------------------------------------------------------------------------


class TestBaselineMode:
    def test_returns_empty_string(self) -> None:
        result = build_system_prompt(mode="baseline")
        assert result == ""

    def test_returns_empty_string_with_repos(self) -> None:
        result = build_system_prompt(mode="baseline", repos=SAMPLE_REPOS)
        assert result == ""


# ---------------------------------------------------------------------------
# mcp_only mode
# ---------------------------------------------------------------------------


class TestMcpOnlyMode:
    def test_includes_mcp_only_header(self) -> None:
        result = build_system_prompt(mode="mcp_only")
        assert "you do not have permission to read them" in result

    def test_does_not_claim_the_workspace_is_empty(self) -> None:
        """The repos ARE cloned into /workspace in every arm; mode_gate denies
        READ on them rather than omitting them (the verifier scores against the
        same files). Telling the agent they are absent describes a container
        that does not exist and was half of bug EnterpriseBench-7rc1 — the agent
        gets `Permission denied`, not `No such file or directory`.
        """
        result = build_system_prompt(mode="mcp_only")
        assert "not present" not in result
        assert "does not contain source code" not in result

    def test_does_not_include_hybrid_header(self) -> None:
        result = build_system_prompt(mode="mcp_only")
        assert "Sourcegraph MCP Tools Available" not in result

    def test_includes_tool_selection(self) -> None:
        result = build_system_prompt(mode="mcp_only")
        assert "Tool Selection" in result

    def test_includes_scoping_rules(self) -> None:
        result = build_system_prompt(mode="mcp_only")
        assert "Scoping (Always Do This)" in result

    def test_includes_efficiency_rules(self) -> None:
        result = build_system_prompt(mode="mcp_only")
        assert "Efficiency Rules" in result

    def test_includes_if_stuck(self) -> None:
        result = build_system_prompt(mode="mcp_only")
        assert "If Stuck" in result

    def test_includes_deepsearch_in_if_stuck(self) -> None:
        result = build_system_prompt(mode="mcp_only")
        assert "deepsearch" in result

    def test_includes_repo_scoping_when_repos_provided(self) -> None:
        result = build_system_prompt(mode="mcp_only", repos=SAMPLE_REPOS)
        assert "sg-evals/" in result
        assert "grpc-go--v1.4.0" in result

    def test_no_repo_scoping_when_no_repos(self) -> None:
        result = build_system_prompt(mode="mcp_only")
        assert "sg-evals/" not in result


# ---------------------------------------------------------------------------
# hybrid mode
# ---------------------------------------------------------------------------


class TestHybridMode:
    def test_includes_hybrid_header(self) -> None:
        result = build_system_prompt(mode="hybrid")
        assert "Sourcegraph MCP Tools Available" in result

    def test_does_not_include_mcp_only_header(self) -> None:
        result = build_system_prompt(mode="hybrid")
        # The hybrid header does NOT contain the mcp_only-specific marker
        assert "You MUST use Sourcegraph MCP tools for all code access" not in result

    def test_includes_mcp_vs_local_table(self) -> None:
        result = build_system_prompt(mode="hybrid")
        assert "Use MCP" in result
        assert "Use Local" in result

    def test_includes_key_rule(self) -> None:
        result = build_system_prompt(mode="hybrid")
        assert "mcp__sourcegraph__read_file" in result

    def test_includes_efficiency_rules(self) -> None:
        result = build_system_prompt(mode="hybrid")
        assert "Efficiency Rules" in result

    def test_includes_if_stuck(self) -> None:
        result = build_system_prompt(mode="hybrid")
        assert "If Stuck" in result

    def test_includes_deepsearch_tool(self) -> None:
        result = build_system_prompt(mode="hybrid")
        assert "deepsearch" in result

    def test_includes_repo_scoping_when_repos_provided(self) -> None:
        result = build_system_prompt(mode="hybrid", repos=SAMPLE_REPOS)
        assert "sg-evals/" in result
        assert "grpc-go--v1.4.0" in result

    def test_includes_workflow_section(self) -> None:
        result = build_system_prompt(mode="hybrid")
        assert "Discover with MCP" in result
        assert "Read locally" in result


# ---------------------------------------------------------------------------
# _build_repo_scope
# ---------------------------------------------------------------------------


class TestBuildRepoScope:
    def test_empty_repos_returns_empty(self) -> None:
        assert _build_repo_scope([]) == ""

    def test_tag_revision_used_as_suffix(self) -> None:
        repos = [
            {"url": "https://github.com/org/repo", "rev": "v2.0.0", "path": "repo"}
        ]
        result = _build_repo_scope(repos)
        assert "sg-evals/repo--v2.0.0" in result

    def test_commit_hash_truncated_to_8(self) -> None:
        repos = [
            {
                "url": "https://github.com/org/repo",
                "rev": "abc123ef90dead",
                "path": "repo",
            }
        ]
        result = _build_repo_scope(repos)
        assert "sg-evals/repo--abc123ef" in result

    def test_slash_in_rev_replaced(self) -> None:
        repos = [
            {"url": "https://github.com/org/repo", "rev": "release/1.0", "path": "repo"}
        ]
        result = _build_repo_scope(repos)
        assert "release_1.0" in result

    def test_includes_local_path(self) -> None:
        repos = [
            {"url": "https://github.com/org/repo", "rev": "v1.0", "path": "my-repo"}
        ]
        result = _build_repo_scope(repos)
        assert "/workspace/my-repo/" in result

    def test_skips_repos_without_url(self) -> None:
        repos = [{"rev": "v1.0", "path": "repo"}]
        result = _build_repo_scope(repos)
        # Header mentions sg-evals but no actual mirror entries should appear
        assert "MCP filter" not in result

    def test_git_suffix_stripped(self) -> None:
        repos = [
            {"url": "https://github.com/org/repo.git", "rev": "v1.0", "path": "repo"}
        ]
        result = _build_repo_scope(repos)
        assert "sg-evals/repo--v1.0" in result


# ---------------------------------------------------------------------------
# Unknown mode
# ---------------------------------------------------------------------------


class TestUnknownMode:
    def test_unknown_mode_returns_empty(self) -> None:
        result = build_system_prompt(mode="unknown_mode")
        assert result == ""
