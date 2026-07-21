"""Tests for --model pinning plumbing in run_task.py.

Covers the argparse surface and TaskRunConfig defaults; the deeper
agent_command-building and results.json/config.json writing behavior is
covered in tests/test_run_task_results.py (via _save_results) and
tests/test_runner_passthrough_contract.py (the run_benchmark -> run_task
forwarding contract).
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(
    0, str(Path(__file__).resolve().parent.parent / "scripts" / "orchestration")
)
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts" / "infra"))

from run_task import TaskRunConfig, _apply_model_flag, build_parser


class TestApplyModelFlag:
    def test_appends_model_flag(self) -> None:
        cmd = _apply_model_flag("claude --dangerously-skip-permissions -p", "claude-sonnet-5")
        assert cmd.endswith("--model claude-sonnet-5")

    def test_reaches_the_default_oauth_command(self) -> None:
        from run_task import DEFAULT_OAUTH_AGENT_COMMAND

        cmd = _apply_model_flag(DEFAULT_OAUTH_AGENT_COMMAND, "claude-sonnet-5")
        assert "--model claude-sonnet-5" in cmd
        # original command is untouched, just extended
        assert cmd.startswith(DEFAULT_OAUTH_AGENT_COMMAND)

    def test_noop_when_model_unset(self) -> None:
        """Do not change default behavior when --model is absent."""
        cmd = "claude -p"
        assert _apply_model_flag(cmd, None) == cmd
        assert _apply_model_flag(cmd, "") == cmd

    def test_noop_when_agent_command_empty(self) -> None:
        assert _apply_model_flag("", "claude-sonnet-5") == ""

    def test_does_not_override_explicit_model(self) -> None:
        cmd = "claude --model claude-opus-4-8 -p"
        assert _apply_model_flag(cmd, "claude-sonnet-5") == cmd

    def test_pins_despite_model_prefixed_flag(self) -> None:
        """A flag that merely starts with "--model" (e.g. --model-config) is
        not an explicit model choice; the old substring test skipped the pin.
        """
        cmd = "claude --model-config /home/agent/mc.json -p"
        assert _apply_model_flag(cmd, "claude-sonnet-5") == (
            cmd + " --model claude-sonnet-5"
        )


class TestModelFlag:
    def test_build_parser_accepts_model(self) -> None:
        parser = build_parser()
        args = parser.parse_args(["dummy/task.toml", "--model", "claude-sonnet-5"])
        assert args.model == "claude-sonnet-5"

    def test_model_defaults_to_none(self) -> None:
        parser = build_parser()
        args = parser.parse_args(["dummy/task.toml"])
        assert args.model is None

    def test_model_does_not_conflict_with_mode_choices(self) -> None:
        """--model is a free string, not constrained to VALID_MODES."""
        parser = build_parser()
        args = parser.parse_args(
            ["dummy/task.toml", "--mode", "mcp_only", "--model", "claude-opus-4-8"]
        )
        assert args.mode == "mcp_only"
        assert args.model == "claude-opus-4-8"


class TestTaskRunConfigModelDefault:
    def test_model_defaults_to_none(self) -> None:
        config = TaskRunConfig(task_toml=Path("/fake/task.toml"))
        assert config.model is None

    def test_model_can_be_set(self) -> None:
        config = TaskRunConfig(task_toml=Path("/fake/task.toml"), model="claude-sonnet-5")
        assert config.model == "claude-sonnet-5"
