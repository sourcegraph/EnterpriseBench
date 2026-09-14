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


class TestToastPreamble:
    """mcp_toast exposes one tool, `toast_search`, backed by Toast-1.

    Its repo scope must be plain store identifiers: Toast takes repo names, not
    the `repo:^...$` query fragment the Sourcegraph preamble renders.
    """

    def _repos(self) -> list[dict]:
        return [
            {
                "url": "https://github.com/open-policy-agent/opa",
                "rev": "v0.69.0",
                "path": "opa",
            }
        ]

    def test_names_toast_search_and_no_sourcegraph_tools(self) -> None:
        prompt = build_system_prompt("mcp_toast", self._repos())

        assert "toast_search" in prompt
        assert "keyword_search" not in prompt
        assert "code_finder" not in prompt

    def test_repo_scope_is_a_bare_identifier(self) -> None:
        prompt = build_system_prompt("mcp_toast", self._repos())

        assert "github.com/sg-evals/opa--v0.69.0" in prompt
        assert "repo:^" not in prompt

    def test_states_the_filesystem_gate(self) -> None:
        prompt = build_system_prompt("mcp_toast", self._repos())

        assert "Permission denied" in prompt

    def test_no_repos_still_yields_a_workflow(self) -> None:
        prompt = build_system_prompt("mcp_toast", [])

        assert "toast_search" in prompt
        assert "Repositories to Search" not in prompt


class TestDefaultMcpPreamble:
    """mcp_default is the server as shipped: every tool, no steering.

    The other three MCP arms each take a position on `code_finder` -- mcp_only
    forbids it, mcp_assisted requires a bootstrap call, mcp_code_finder forces
    one call per repo. All three therefore measure the tool under instruction.
    This arm removes the instruction so the comparison can answer what a
    customer gets by pointing an agent at the default endpoint.
    """

    def _repos(self) -> list[dict]:
        return [
            {
                "url": "https://github.com/open-policy-agent/opa",
                "rev": "v0.69.0",
                "path": "opa",
            }
        ]

    def test_offers_code_finder_without_requiring_it(self) -> None:
        prompt = build_system_prompt("mcp_default", self._repos())

        assert "code_finder" in prompt
        assert "Do not call `code_finder`" not in prompt
        assert "Required Code Finder Workflow" not in prompt
        assert "Required Code Finder Bootstrap" not in prompt

    def test_keeps_the_direct_retrieval_tools(self) -> None:
        prompt = build_system_prompt("mcp_default", self._repos())

        for tool in ("keyword_search", "nls_search", "read_file"):
            assert tool in prompt

    def test_states_the_filesystem_gate(self) -> None:
        prompt = build_system_prompt("mcp_default", self._repos())

        assert "Permission denied" in prompt

    def test_shares_the_common_sections_with_mcp_only(self) -> None:
        default = build_system_prompt("mcp_default", self._repos())
        direct = build_system_prompt("mcp_only", self._repos())

        assert "Tool Selection" in default
        assert "Scoping (Always Do This)" in default
        # The arms differ in exactly one section: the code_finder directive.
        assert default != direct
        assert "Direct MCP Arm" not in default


class TestTreatmentArmSharesTheDefaultPreamble:
    """mcp_treatment must vary the server, and nothing else.

    If the preamble differed at all, a shift in code_finder adoption could be
    attributed to our prompt rather than to Sourcegraph's tool descriptions,
    which is the only thing the arm exists to measure.
    """

    @staticmethod
    def _repos() -> list[dict]:
        return [{"url": "https://github.com/pallets/flask", "rev": "main", "path": "flask"}]

    def test_preamble_is_byte_identical_to_mcp_default(self) -> None:
        assert build_system_prompt("mcp_treatment", repos=self._repos()) == (
            build_system_prompt("mcp_default", repos=self._repos())
        )

    def test_preamble_is_not_empty(self) -> None:
        assert build_system_prompt("mcp_treatment", repos=self._repos()).strip()
