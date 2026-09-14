"""Tests for the zero-sgx-call gate and sgx-usage counting in run_task.py.

The ``cli`` arm's only measured retrieval path is the ``sgx`` shell command. A
cli run that made 0 sgx calls used only local tools, so whatever it scored it did
not exercise the retrieval the arm exists to measure. It is marked INVALID and
routed to the infra-error re-run channel — the exact analog of
``_route_zero_mcp_run`` (EnterpriseBench-ybge9, scope-add (2) of 7rc1).

Unlike ``mcp_only``, the cli arm is NOT gated at the filesystem: local source is
present by design (EnterpriseBench-83lg6). That is precisely why the usage gate
matters here — nothing else stops a run from ignoring sgx entirely and still
looking like a valid CLI measurement.

``sgx`` is a Bash command, not an MCP tool, so the counter parses Bash tool_use
``input.command`` rather than a tool-name prefix. The matcher and the negatives
below are calibrated against 60 real cli-arm traces (CSB:runs/stratum_cliv1):
728/728 real sgx calls matched, 0 misses, 0 false positives, 0/60 runs
false-zeroed.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

sys.path.insert(
    0, str(Path(__file__).resolve().parent.parent / "scripts" / "orchestration")
)

import run_task
from run_task import (
    RUN_STATUS_INVALID,
    TaskRunResult,
    _route_zero_sgx_run,
)


def _result(sgx_calls: int) -> TaskRunResult:
    return TaskRunResult(
        task_id="ccx-dep-trace-253",
        phase="",
        tool_usage={"sgx_tool_calls": sgx_calls},
    )


class TestZeroSgxGateCli:
    """cli with 0 sgx calls is an infra failure, not a score."""

    def test_marks_run_invalid(self) -> None:
        result = _result(0)

        _route_zero_sgx_run(result, "cli")

        assert result.status == RUN_STATUS_INVALID
        assert result.failure_class == "infra_sgx_unused"
        assert result.success is False
        assert result.tool_usage["sgx_used"] is False

    def test_phase_lands_outside_complete(self) -> None:
        result = _result(0)

        _route_zero_sgx_run(result, "cli")

        assert result.phase == "agent_infra_error"
        assert result.phase != "complete"

    def test_records_a_reason_on_the_result(self) -> None:
        result = _result(0)

        _route_zero_sgx_run(result, "cli")

        assert "cli" in result.error
        assert result.error != ""

    def test_does_not_relabel_an_already_invalid_run(self) -> None:
        """A broken sandbox is the root cause; 0 sgx calls is just its symptom.

        A run that failed earlier (its status already INVALID with a specific
        cause) then trivially made 0 sgx calls. Relabelling it infra_sgx_unused
        would bury the real cause and send triage chasing a phantom sgx problem.
        """
        result = _result(0)
        result.status = RUN_STATUS_INVALID
        result.failure_class = "infra_sandbox_setup"
        result.phase = "agent_infra_error"

        _route_zero_sgx_run(result, "cli")

        assert result.failure_class == "infra_sandbox_setup"
        assert result.status == RUN_STATUS_INVALID
        assert result.tool_usage["sgx_used"] is False

    @pytest.mark.parametrize("root_cause", ["infra_oom", "infra_timeout", "agent_error"])
    def test_preserves_a_root_cause_that_never_set_status(
        self, root_cause: str
    ) -> None:
        """OOM / timeout / crash set failure_class but NOT status.

        Each trivially produces 0 sgx calls, so a status-only guard would relabel
        them infra_sgx_unused and hide the real cause. The run must still be
        excluded, but keep its real cause.
        """
        result = _result(0)
        result.failure_class = root_cause

        _route_zero_sgx_run(result, "cli")

        assert result.failure_class == root_cause
        assert result.status == RUN_STATUS_INVALID  # still excluded
        assert result.phase == "agent_infra_error"
        assert result.success is False

    def test_run_with_sgx_calls_is_untouched(self) -> None:
        result = _result(9)

        _route_zero_sgx_run(result, "cli")

        assert result.status == ""
        assert result.phase == ""
        assert result.failure_class is None
        assert result.tool_usage["sgx_used"] is True


class TestZeroSgxGateOtherArms:
    """Only the cli arm is gated on sgx — every other arm is left untouched.

    baseline/mcp_only/hybrid do not measure sgx retrieval, so 0 sgx there is not
    a failure and the gate must not flag or invalidate them (that is the mcp
    gate's job for mcp_only/hybrid, keyed on a different counter).
    """

    @pytest.mark.parametrize("mode", ["baseline", "mcp_only", "hybrid"])
    @pytest.mark.parametrize("sgx_calls", [0, 5])
    def test_non_cli_arm_is_never_gated_or_flagged(
        self, mode: str, sgx_calls: int
    ) -> None:
        result = _result(sgx_calls)

        _route_zero_sgx_run(result, mode)

        assert result.status == ""
        assert result.phase == ""
        assert result.failure_class is None
        assert "sgx_used" not in result.tool_usage


def _record(kind: str, *blocks: dict, **top: object) -> str:
    """A stream-json record of the given type, carrying content blocks.

    ``top`` sets record-level fields (e.g. ``parent_tool_use_id`` for a tool call
    that Claude Code inlined from a Task subagent into the parent stream).
    """
    return json.dumps({"type": kind, "message": {"content": list(blocks)}, **top})


def _assistant(*blocks: dict, **top: object) -> str:
    return _record("assistant", *blocks, **top)


def _bash(command: str) -> dict:
    return {
        "type": "tool_use",
        "id": "toolu_01ABC",
        "name": "Bash",
        "input": {"command": command},
    }


def _usage(tmp_path: Path, stdout: str) -> dict:
    (tmp_path / "agent_stdout.log").write_text(stdout)
    return run_task._extract_tool_usage(tmp_path)


# Real command forms lifted from CSB:runs/stratum_cliv1 (the calibration corpus).
# Every one is a genuine sgx invocation the counter MUST see; a miss here would
# false-zero a valid cli run and destroy a real measurement.
REAL_SGX_COMMANDS = [
    "sgx search 'VerbAll repo:^github.com/sg-evals/kubernetes--v1.32.0$'",
    "sgx search 'q1' -q 'q2' -q 'q3'",
    "sgx read kubernetes path/to/file.go --start 10 --end 40",
    "sgx def kubernetes pkg/apis/rbac/types.go RuleAllows",
    "sgx refs kubernetes pkg/apis/rbac/types.go VisitRulesFor",
    "sgx nls 'how does rbac authorization work'",
    "sgx ls kubernetes pkg/apis",
    "sgx search 'foo' | head -40",
]

# Bash commands that co-occur in the same traces and are NOT sgx. Counting any of
# these would let a local-tools-only run masquerade as a valid cli measurement.
NON_SGX_COMMANDS = [
    "grep -E '^# github' /tmp/envoy_v2.txt | sort -u",
    "python3 -c \"import json; json.load(open('/workspace/answer.json'))\"",
    "ls /workspace/",
    "cat /tmp/files_preview.json",
    "echo 'run sgx next'",  # names sgx in a string, invokes nothing
    "cd /workspace/sgx-cache && grep foo bar",  # 'sgx' only inside a path
]


class TestSgxToolCallCounting:
    """sgx_tool_calls counts genuine sgx Bash invocations — never a mention."""

    @pytest.mark.parametrize("command", REAL_SGX_COMMANDS)
    def test_real_sgx_invocation_counts(self, tmp_path: Path, command: str) -> None:
        assert _usage(tmp_path, _assistant(_bash(command)))["sgx_tool_calls"] == 1

    @pytest.mark.parametrize("command", NON_SGX_COMMANDS)
    def test_non_sgx_bash_does_not_count(self, tmp_path: Path, command: str) -> None:
        assert _usage(tmp_path, _assistant(_bash(command)))["sgx_tool_calls"] == 0

    def test_sgx_after_a_shell_separator_counts(self, tmp_path: Path) -> None:
        """sgx chained after cd/&& is still an invocation."""
        stream = _assistant(_bash("cd /workspace && sgx search 'foo'"))
        assert _usage(tmp_path, stream)["sgx_tool_calls"] == 1

    def test_sgx_in_command_substitution_counts(self, tmp_path: Path) -> None:
        stream = _assistant(_bash("echo \"$(sgx refs kubernetes f.go Sym)\""))
        assert _usage(tmp_path, stream)["sgx_tool_calls"] == 1

    def test_a_non_bash_tool_named_like_sgx_does_not_count(
        self, tmp_path: Path
    ) -> None:
        """Only Bash tool_use is inspected; the command text lives there."""
        block = {"type": "tool_use", "id": "t", "name": "Read", "input": {"file": "sgx.md"}}
        assert _usage(tmp_path, _assistant(block))["sgx_tool_calls"] == 0

    def test_subagent_inlined_sgx_call_counts(self, tmp_path: Path) -> None:
        """A Task subagent's sgx call is inlined into the parent stream, tagged
        with parent_tool_use_id. Counting all records (not just parent-owned
        ones) is what keeps a compliant subagent run from false-zeroing."""
        stream = _assistant(_bash("sgx search 'foo'"), parent_tool_use_id="toolu_parent")
        assert _usage(tmp_path, stream)["sgx_tool_calls"] == 1

    def test_multiple_sgx_calls_across_records_accumulate(self, tmp_path: Path) -> None:
        stream = (
            _assistant(_bash("sgx search 'a'"))
            + "\n"
            + _assistant(_bash("sgx read repo f --start 1 --end 9"), _bash("ls"))
            + "\n"
        )
        assert _usage(tmp_path, stream)["sgx_tool_calls"] == 2

    def test_chained_sgx_calls_in_one_command_each_count(self, tmp_path: Path) -> None:
        """A Bash block that chains several sgx calls counts each, not the block."""
        stream = _assistant(_bash("sgx search 'a' && sgx read r f --start 1 --end 9"))
        assert _usage(tmp_path, stream)["sgx_tool_calls"] == 2

    def test_adversarial_command_string_returns_promptly(self, tmp_path: Path) -> None:
        """A slash-heavy command must not drive the matcher quadratic.

        input.command is agent-authored trace text parsed synchronously in the
        orchestrator; an unbounded path scan would let one large command stall
        every task's post-processing (would-be ReDoS). The bounded scan keeps this
        linear — assert it finishes far under any run timeout.
        """
        import time

        adversarial = (";" + "/" * 20) * 20000  # 420k chars, no sgx present
        start = time.perf_counter()
        usage = _usage(tmp_path, _assistant(_bash(adversarial)))
        assert time.perf_counter() - start < 2.0
        assert usage["sgx_tool_calls"] == 0

    def test_missing_log_reports_zero_sgx(self, tmp_path: Path) -> None:
        assert run_task._extract_tool_usage(tmp_path)["sgx_tool_calls"] == 0

    def test_whole_file_json_reports_zero_sgx(self, tmp_path: Path) -> None:
        """--output-format json emits one result object and no tool_use records.

        The parse loop is shared with mcp counting; this pins that sgx counting
        survives the whole-file-JSON branch of _iter_agent_records too.
        """
        payload = json.dumps(
            {"result": "I ran sgx search then read the file", "numTurns": 3}
        )
        assert _usage(tmp_path, payload)["sgx_tool_calls"] == 0

    def test_noisy_interleaved_log_counts_sgx(self, tmp_path: Path) -> None:
        """Container logs interleave plain-text lines with the JSON stream."""
        stream = (
            "starting agent...\n"
            + _assistant(_bash("sgx search 'foo'"))
            + "\n"
            "not json at all\n"
            '{"broken": \n'
            + json.dumps({"num_turns": 4})
            + "\n"
        )
        assert _usage(tmp_path, stream)["sgx_tool_calls"] == 1

    def test_codex_command_execution_counts_sgx(self, tmp_path: Path) -> None:
        stream = json.dumps(
            {
                "type": "item.completed",
                "item": {
                    "type": "command_execution",
                    "command": (
                        "sgx search 'register_blueprint' && "
                        "sgx read flask src/flask/sansio/blueprints.py"
                    ),
                    "status": "completed",
                },
            }
        )

        assert _usage(tmp_path, stream)["sgx_tool_calls"] == 2

    def test_codex_bash_lc_wrapper_counts_leading_sgx(self, tmp_path: Path) -> None:
        stream = json.dumps(
            {
                "type": "item.completed",
                "item": {
                    "type": "command_execution",
                    "command": (
                        "/bin/bash -lc \"sgx search 'register_blueprint' && "
                        'sgx read flask src/flask/sansio/blueprints.py"'
                    ),
                    "status": "completed",
                },
            }
        )

        assert _usage(tmp_path, stream)["sgx_tool_calls"] == 2

    def test_codex_bash_lc_wrapper_does_not_count_a_mention(
        self, tmp_path: Path
    ) -> None:
        stream = json.dumps(
            {
                "type": "item.completed",
                "item": {
                    "type": "command_execution",
                    "command": "/bin/bash -lc 'echo \"run sgx next\"'",
                    "status": "completed",
                },
            }
        )

        assert _usage(tmp_path, stream)["sgx_tool_calls"] == 0

    def test_opencode_bash_tool_counts_sgx(self, tmp_path: Path) -> None:
        stream = json.dumps(
            {
                "type": "tool_use",
                "part": {
                    "type": "tool",
                    "tool": "bash",
                    "state": {
                        "status": "completed",
                        "input": {
                            "command": "sgx search 'register_blueprint' | head -40"
                        },
                    },
                },
            }
        )

        assert _usage(tmp_path, stream)["sgx_tool_calls"] == 1

    @pytest.mark.parametrize(
        "record",
        [
            {
                "type": "item.completed",
                "item": {
                    "type": "agent_message",
                    "text": "I should run sgx search next.",
                },
            },
            {
                "type": "tool_use",
                "part": {
                    "type": "tool",
                    "tool": "read",
                    "state": {
                        "status": "completed",
                        "input": {"filePath": "/workspace/sgx-notes.md"},
                    },
                },
            },
        ],
    )
    def test_generated_harness_sgx_mentions_do_not_count(
        self, tmp_path: Path, record: dict
    ) -> None:
        assert _usage(tmp_path, json.dumps(record))["sgx_tool_calls"] == 0
