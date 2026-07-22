"""A non-zero agent exit must invalidate the run on EVERY arm (rryas.7).

Before the fix, ``run_task.py`` flagged a non-zero agent exit with only a
``failure_class`` and left ``phase`` unprotected, so the save-time
``phase='complete'`` overwrite recorded the run as a valid score. mcp_only/cli
were rescued only incidentally by the zero-tool gates (a dead agent makes 0
MCP/sgx calls); baseline has no such gate, so a rate-limited baseline was
counted as a valid 0 — surfaced live by the rryas.1 shakeout (a 429 session
limit on the baseline arm recorded ``status=valid, phase=complete``).

``_route_agent_exit`` now routes any non-zero exit to
``phase='agent_infra_error'`` + ``status=INVALID`` uniformly, and labels a
session-limit/429 ``infra_rate_limit`` (transient, re-runnable) vs a plain
``agent_error``.
"""

from __future__ import annotations

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
    _effective_status,
    _route_agent_exit,
    _route_zero_mcp_run,
    _scan_agent_rate_limited,
)

RATE_LIMIT_STDOUT = (
    '{"type":"result","subtype":"success","is_error":true,'
    '"api_error_status":429,"result":"You\'ve hit your session limit · '
    'resets 12:40am (UTC)","terminal_reason":"api_error"}'
)


def _out_with_stdout(tmp_path: Path, text: str) -> Path:
    (tmp_path / "agent_stdout.log").write_text(text)
    return tmp_path


class TestRouteAgentExit:
    def test_clean_exit_leaves_run_untouched(self, tmp_path: Path) -> None:
        r = TaskRunResult(task_id="t")
        _route_agent_exit(r, 0, tmp_path)
        assert r.status == ""
        assert r.phase == ""
        assert r.failure_class is None
        assert _effective_status(r) != RUN_STATUS_INVALID or r.phase != "complete"

    @pytest.mark.parametrize(
        "code,expected",
        [(137, "infra_oom"), (124, "infra_timeout")],
    )
    def test_oom_and_timeout_are_invalid(
        self, code: int, expected: str, tmp_path: Path
    ) -> None:
        r = TaskRunResult(task_id="t")
        _route_agent_exit(r, code, tmp_path)
        assert r.failure_class == expected
        assert r.status == RUN_STATUS_INVALID
        assert r.phase == "agent_infra_error"
        assert r.success is False

    def test_rate_limited_exit_is_infra_rate_limit(self, tmp_path: Path) -> None:
        out = _out_with_stdout(tmp_path, RATE_LIMIT_STDOUT)
        r = TaskRunResult(task_id="t")
        _route_agent_exit(r, 1, out)
        assert r.failure_class == "infra_rate_limit"
        assert r.status == RUN_STATUS_INVALID
        assert r.phase == "agent_infra_error"

    def test_plain_nonzero_exit_is_agent_error(self, tmp_path: Path) -> None:
        out = _out_with_stdout(tmp_path, '{"type":"result","is_error":true}')
        r = TaskRunResult(task_id="t")
        _route_agent_exit(r, 1, out)
        assert r.failure_class == "agent_error"
        assert r.status == RUN_STATUS_INVALID
        assert r.phase == "agent_infra_error"

    def test_max_turns_exit_is_a_scored_outcome_not_infra(
        self, tmp_path: Path
    ) -> None:
        """Exhausting the turn budget is the agent's real, measured failure.
        Routing it INVALID re-runs the weakest cells until they happen to
        finish, censoring scores upward — and hits baseline hardest on hard
        tasks. The session terminated cleanly at budget: score what's there."""
        out = _out_with_stdout(
            tmp_path,
            '{"type":"result","subtype":"error_max_turns","is_error":true,'
            '"num_turns":51,"terminal_reason":"max_turns",'
            '"errors":["Reached maximum number of turns (50)"]}',
        )
        r = TaskRunResult(task_id="t")
        _route_agent_exit(r, 1, out)
        assert r.failure_class == "agent_max_turns"
        assert r.status == ""  # NOT invalid — proceeds to scoring
        assert r.phase == ""  # not routed to the infra re-run channel

    def test_rate_limit_beats_max_turns_marker_noise(
        self, tmp_path: Path
    ) -> None:
        # A terminal rate-limit is infra even if earlier content mentioned
        # max turns; only the provider-emitted terminal subtype counts.
        out = _out_with_stdout(
            tmp_path, RATE_LIMIT_STDOUT + "\nReached maximum number of turns"
        )
        r = TaskRunResult(task_id="t")
        _route_agent_exit(r, 1, out)
        assert r.failure_class == "infra_rate_limit"
        assert r.status == RUN_STATUS_INVALID


class TestBaselineRegression:
    """The core rryas.7 defect: baseline has no zero-tool gate to catch a bad
    exit, so the exit itself must invalidate the run."""

    def test_baseline_nonzero_exit_is_invalid_and_save_proof(
        self, tmp_path: Path
    ) -> None:
        out = _out_with_stdout(tmp_path, RATE_LIMIT_STDOUT)
        r = TaskRunResult(task_id="t")
        _route_agent_exit(r, 1, out)

        # No zero-tool gate runs for baseline; the exit alone must exclude it.
        assert _effective_status(r) == RUN_STATUS_INVALID
        # phase is in NON_COMPLETE_PHASES, so the save-time overwrite that sets
        # phase='complete'/success=True is skipped and cannot resurrect it.
        assert r.phase in run_task.NON_COMPLETE_PHASES

    def test_effective_status_would_have_been_valid_without_phase(self) -> None:
        """Guards the exact bug shape: a non-invalid status + phase='complete'
        derives to VALID — which is what the old code saved."""
        stale = TaskRunResult(task_id="t")
        stale.phase = "complete"
        stale.success = True
        assert _effective_status(stale) == ""  # the pre-fix false-valid outcome


class TestZeroToolGatePreservesLabel:
    """A rate-limited mcp_only run must keep infra_rate_limit, not be relabelled
    infra_mcp_unused by the zero-MCP gate that runs afterwards."""

    def test_mcp_only_rate_limit_label_survives_zero_mcp_gate(
        self, tmp_path: Path
    ) -> None:
        out = _out_with_stdout(tmp_path, RATE_LIMIT_STDOUT)
        r = TaskRunResult(task_id="t", tool_usage={"mcp_tool_calls": 0})
        _route_agent_exit(r, 1, out)
        assert r.failure_class == "infra_rate_limit"

        _route_zero_mcp_run(r, "mcp_only")  # 0 mcp calls, but already classified

        assert r.failure_class == "infra_rate_limit"  # not relabelled
        assert r.status == RUN_STATUS_INVALID


class TestScanAgentRateLimited:
    @pytest.mark.parametrize(
        "marker",
        [
            '"error":"rate_limit"',
            "You've hit your session limit · resets 12:40am (UTC)",
            "session limit · resets 1:00am (UTC)",
        ],
    )
    def test_detects_provider_markers(self, marker: str, tmp_path: Path) -> None:
        assert _scan_agent_rate_limited(_out_with_stdout(tmp_path, marker)) is True

    def test_missing_log_is_false(self, tmp_path: Path) -> None:
        assert _scan_agent_rate_limited(tmp_path) is False

    def test_bare_429_in_agent_content_is_not_a_match(self, tmp_path: Path) -> None:
        """A 429 the agent merely wrote (e.g. an HTTP status in its own output)
        is not a provider rate-limit signal and must not be misclassified."""
        benign = '{"type":"assistant","message":{"content":[{"type":"text",'
        benign += '"text":"the endpoint returned HTTP 429 once during testing"}]}}'
        assert _scan_agent_rate_limited(_out_with_stdout(tmp_path, benign)) is False
