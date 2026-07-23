"""Tests for the benchmark-sandbox tool lockdown in run_task.py.

Root cause (2026-07-22, root-causing the mcp_only sweep): the container's
`claude` inherited the full host/fleet tool registry -- WebSearch/WebFetch
(external answer lookup, contamination), Task/Agent (sub-agent spawning that
runs OUTSIDE the measured trace and OUTSIDE mode_gate), ToolSearch-discoverable
orchestration tools (ScheduleWakeup, SendMessage, Cron*, Monitor,
RemoteTrigger, PushNotification, DesignSync, Workflow, Skill, EnterWorktree/
ExitWorktree, TaskCreate/TaskGet/TaskList/TaskOutput/TaskStop, ReportFindings,
NotebookEdit). 69% of sweep cells (205/299) called at least one. Empirically
verified live (2026-07-22): `--disallowedTools` makes ToolSearch itself report
"No matching deferred tools found" for a disallowed name -- it is not merely a
late invocation-time deny, the tool becomes undiscoverable. `mcp__sourcegraph__*`
tools are genuinely deferred (100% of mcp_only cells called ToolSearch before
their first MCP call), so ToolSearch itself must stay allowed.
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(
    0, str(Path(__file__).resolve().parent.parent / "scripts" / "orchestration")
)
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts" / "infra"))

from run_task import (
    BENCHMARK_DISALLOWED_TOOLS,
    DEFAULT_OAUTH_AGENT_COMMAND,
    _apply_disallowed_tools,
    _SAFE_AGENT_CMD_RE,
)


class TestBenchmarkDisallowedTools:
    def test_covers_web_tools(self) -> None:
        assert "WebSearch" in BENCHMARK_DISALLOWED_TOOLS
        assert "WebFetch" in BENCHMARK_DISALLOWED_TOOLS

    def test_covers_subagent_spawning(self) -> None:
        """Task/Agent sub-agents run outside the measured trace AND outside
        mode_gate -- a gated arm's sub-agent could read local source or hit
        the web with none of it counted. Worst single contaminant."""
        assert "Task" in BENCHMARK_DISALLOWED_TOOLS

    def test_covers_fleet_orchestration_tools(self) -> None:
        for name in (
            "ScheduleWakeup",
            "SendMessage",
            "Monitor",
            "CronCreate",
            "CronDelete",
            "CronList",
            "RemoteTrigger",
            "PushNotification",
            "DesignSync",
            "Workflow",
            "Skill",
            "EnterWorktree",
            "ExitWorktree",
            "TaskCreate",
            "TaskGet",
            "TaskList",
            "TaskOutput",
            "TaskStop",
            "ReportFindings",
            "NotebookEdit",
        ):
            assert name in BENCHMARK_DISALLOWED_TOOLS, f"{name} not disallowed"

    def test_does_not_disallow_core_retrieval_tools(self) -> None:
        """The tools every arm's real work runs through must stay untouched."""
        for name in ("Bash", "Read", "Write", "Edit", "ToolSearch"):
            assert name not in BENCHMARK_DISALLOWED_TOOLS, (
                f"{name} must stay allowed -- it is core retrieval/deliverable "
                f"machinery, not contamination"
            )

    def test_is_comma_joinable_with_no_embedded_comma(self) -> None:
        # Each tool name itself must not contain a comma (would corrupt the join)
        assert all("," not in t for t in BENCHMARK_DISALLOWED_TOOLS)


class TestApplyDisallowedTools:
    def test_appends_disallowed_tools_flag(self) -> None:
        cmd = _apply_disallowed_tools("claude --dangerously-skip-permissions -p")
        assert "--disallowedTools" in cmd
        for name in ("WebSearch", "Task", "ScheduleWakeup"):
            assert name in cmd

    def test_reaches_the_default_oauth_command(self) -> None:
        cmd = _apply_disallowed_tools(DEFAULT_OAUTH_AGENT_COMMAND)
        assert "--disallowedTools" in cmd
        assert cmd.startswith(DEFAULT_OAUTH_AGENT_COMMAND)

    def test_noop_when_agent_command_empty(self) -> None:
        assert _apply_disallowed_tools("") == ""

    def test_does_not_override_an_explicit_disallowedtools(self) -> None:
        """Never override a caller-supplied explicit choice (mirrors
        _apply_model_flag's contract)."""
        cmd = "claude --disallowedTools Bash -p"
        assert _apply_disallowed_tools(cmd) == cmd

    def test_not_fooled_by_a_prefixed_flag(self) -> None:
        cmd = "claude --disallowedToolsExtra foo -p"
        result = _apply_disallowed_tools(cmd)
        assert result != cmd
        assert "--disallowedTools" in result

    def test_composes_with_model_flag(self) -> None:
        from run_task import _apply_model_flag

        cmd = _apply_model_flag(DEFAULT_OAUTH_AGENT_COMMAND, "claude-sonnet-5")
        cmd = _apply_disallowed_tools(cmd)
        assert "--model claude-sonnet-5" in cmd
        assert "--disallowedTools" in cmd
        assert _SAFE_AGENT_CMD_RE.match(cmd), (
            f"composed command fails the safety regex: {cmd!r}"
        )

    def test_result_passes_the_safe_command_regex(self) -> None:
        cmd = _apply_disallowed_tools(DEFAULT_OAUTH_AGENT_COMMAND)
        assert _SAFE_AGENT_CMD_RE.match(cmd), (
            f"disallowed-tools command fails _SAFE_AGENT_CMD_RE: {cmd!r}"
        )


class TestSafeCommandRegexAllowsCommas:
    """_SAFE_AGENT_CMD_RE must widen to allow the comma --disallowedTools
    needs, without opening shell metacharacters (;|&$`(){}<>\\'\"\\n)."""

    def test_allows_comma_separated_tool_list(self) -> None:
        assert _SAFE_AGENT_CMD_RE.match(
            "claude --disallowedTools WebSearch,WebFetch,Task -p"
        )

    def test_still_rejects_shell_metacharacters(self) -> None:
        for bad in (
            "claude -p; rm -rf /",
            "claude -p `whoami`",
            "claude -p $(whoami)",
            "claude -p && echo pwned",
            "claude -p | cat /etc/passwd",
            "claude -p > /tmp/x",
        ):
            assert not _SAFE_AGENT_CMD_RE.match(bad), f"should reject: {bad!r}"
