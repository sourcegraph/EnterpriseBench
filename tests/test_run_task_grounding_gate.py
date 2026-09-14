"""Tests for the grounded-citations gate wiring in run_task.py.

Before this wiring, ground_truth.require_grounded_citations reached only
_build_instruction_text — it shaped the agent's prompt and nothing else. The
gate at eb_verify/runner.py was reachable only from eb_verify.cli, which no
production orchestrator calls, so the flag was inert in every Docker run.

The regressions these tests exist to catch, in order of how quietly they fail:

  - The judge recomputes scores["task_score"] from checkpoints. A gate applied
    before Phase 5b is silently erased. Both tasks that set the flag today
    declare llm_curator, so this is a live path, not a hypothetical.
  - Zeroing task_score while leaving all_passed=True yields a results.json that
    contradicts itself; solve_verify reads both fields.
  - Forcing a gate zero over an infra error would relabel an invalid run as an
    agent failure and burn the re-run channel.
  - Gating a task that never asked for it would silently rescore the other
    110 tasks.
"""

from __future__ import annotations

import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts" / "orchestration"))
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts" / "infra"))
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "lib"))

from run_task import (  # noqa: E402
    GROUNDING_GATE_KEY,
    TaskRunResult,
    _apply_grounding_gate,
    _grounding_required,
    _run_scoring,
)


def _result(phase: str = "") -> TaskRunResult:
    return TaskRunResult(task_id="t", phase=phase)


def _passing_scores(**gate) -> dict:
    return {
        "task_score": 1.0,
        "all_passed": True,
        "checkpoints": [{"name": "c1", "weight": 1.0, "score": 1.0, "passed": True}],
        GROUNDING_GATE_KEY: {"passed": True, "detail": "score=1.00", **gate},
    }


# ---------------------------------------------------------------------------
# _grounding_required
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "task_data,expected",
    [
        ({"ground_truth": {"require_grounded_citations": True}}, True),
        ({"ground_truth": {"require_grounded_citations": False}}, False),
        ({"ground_truth": {}}, False),
        ({}, False),
        ({"ground_truth": None}, False),
    ],
)
def test_grounding_required(task_data, expected):
    assert _grounding_required(task_data) is expected


# ---------------------------------------------------------------------------
# Applying the verdict
# ---------------------------------------------------------------------------


def test_failed_gate_zeroes_score_and_all_passed():
    scores = _passing_scores()
    scores[GROUNDING_GATE_KEY] = {"passed": False, "detail": "ungrounded citations: score=0.00"}
    result = _result()

    _apply_grounding_gate(result, scores)

    assert scores["task_score"] == 0.0
    # HIGH-3: leaving all_passed True makes results.json self-contradictory —
    # solve_verify.py reads both and would report a pass at score zero.
    assert scores["all_passed"] is False
    assert any("require_grounded_citations" in g for g in scores["score_gates"])


def test_passing_gate_leaves_score_untouched():
    scores = _passing_scores()
    _apply_grounding_gate(_result(), scores)
    assert scores["task_score"] == 1.0
    assert scores["all_passed"] is True
    assert not scores.get("score_gates")


def test_gate_absent_leaves_score_untouched():
    """The 110 tasks that never set the flag must keep legacy scoring exactly."""
    scores = {"task_score": 0.83, "all_passed": True}
    _apply_grounding_gate(_result(), scores)
    assert scores["task_score"] == 0.83
    assert scores["all_passed"] is True
    assert "score_gates" not in scores


def test_gate_survives_the_judge_recompute():
    """Phase 5c must run AFTER Phase 5b.

    _apply_llm_judge rewrites scores["task_score"] as the weighted sum of the
    checkpoints. This reproduces that rewrite and asserts the gate still wins —
    if the gate were applied before the judge, the forced 0.0 would be erased
    and the run would bank a full score on fabricated citations.
    """
    scores = _passing_scores()
    scores[GROUNDING_GATE_KEY] = {"passed": False, "detail": "ungrounded citations"}

    # Stand in for _apply_llm_judge's checkpoint-weighted recompute.
    scores["task_score"] = sum(
        c["score"] * c["weight"] for c in scores["checkpoints"]
    )
    assert scores["task_score"] == 1.0

    _apply_grounding_gate(_result(), scores)

    assert scores["task_score"] == 0.0


@pytest.mark.parametrize("phase", ["verifier_infra_error", "integrity_violation"])
def test_gate_skipped_on_untrusted_phase(phase):
    """Mirrors runner.py: an invalid run has no score for a gate to force down.

    Stamping a gate zero over an infra error would relabel a broken harness as
    a measured agent failure and deny the run its re-run.
    """
    scores = {"task_score": 0.0, "all_passed": False}
    scores[GROUNDING_GATE_KEY] = {"passed": False, "detail": "ungrounded"}
    result = _result(phase=phase)

    _apply_grounding_gate(result, scores)

    assert "score_gates" not in scores
    assert result.phase == phase


def test_gate_infra_error_routes_to_rerun_not_a_zero():
    """A gate that could not run is not evidence the agent cheated.

    Folding it into a 0.0 would be the exact conflation guard_verifier_output
    exists to prevent.
    """
    scores = _passing_scores()
    scores[GROUNDING_GATE_KEY] = {
        "infra_error": {"reason": "grounding_gate_unreadable", "detail": "boom"}
    }
    result = _result()

    _apply_grounding_gate(result, scores)

    assert result.phase == "verifier_infra_error"
    assert scores["verifier_infra_error"]["reason"] == "grounding_gate_unreadable"
    # Not rewritten to a fake zero.
    assert scores["task_score"] == 1.0
    assert "score_gates" not in scores


# ---------------------------------------------------------------------------
# Deciding the verdict inside the closed scoring window
# ---------------------------------------------------------------------------


def _exec_ok(stdout: str = "") -> MagicMock:
    m = MagicMock()
    m.returncode = 0
    m.stdout = stdout
    m.stderr = ""
    return m


VERIFIER_JSON = (
    '{"task_score": 1.0, "all_passed": true, '
    '"checkpoints": [{"name": "c1", "weight": 1.0, "score": 1.0, '
    '"passed": true, "verifier_ran": true}]}'
)


def test_gate_runs_inside_the_closed_workspace_window():
    """The gate must read the answer in the same window the checks do.

    _run_scoring closes /workspace (1755), runs test.sh, and reopens it (1777)
    in a finally. A gate running after the reopen would read the graded artifact
    from a world-writable workspace — a strictly weaker guarantee than the bash
    checks get, on the one artifact whose authenticity is the entire point.
    """
    calls: list[str] = []

    def exec_side_effect(container_id, cmd, **kwargs):
        joined = " ".join(cmd)
        if "test.sh" in joined:
            calls.append("test.sh")
            return _exec_ok(VERIFIER_JSON)
        if "eb_verify.artifact_gate" in joined:
            calls.append("gate")
            return _exec_ok('{"passed": true, "detail": "score=1.00"}')
        return _exec_ok()

    with patch("run_task._assert_grading_assets_sealed", return_value=(True, "")), patch(
        "run_task._reap_agent_processes"
    ), patch("run_task._close_workspace_for_scoring", side_effect=lambda c: calls.append("close")), patch(
        "run_task._reopen_workspace_for_agent", side_effect=lambda c: calls.append("reopen")
    ), patch(
        "run_task._docker_exec", side_effect=exec_side_effect
    ):
        scores = _run_scoring("cid", require_grounded=True)

    assert calls == ["close", "test.sh", "gate", "reopen"]
    assert scores[GROUNDING_GATE_KEY]["passed"] is True


def test_gate_not_executed_when_flag_absent():
    """No flag, no gate exec — and no cost for the tasks that never asked."""
    execs: list[str] = []

    def exec_side_effect(container_id, cmd, **kwargs):
        execs.append(" ".join(cmd))
        return _exec_ok(VERIFIER_JSON)

    with patch("run_task._assert_grading_assets_sealed", return_value=(True, "")), patch(
        "run_task._reap_agent_processes"
    ), patch("run_task._close_workspace_for_scoring"), patch(
        "run_task._reopen_workspace_for_agent"
    ), patch(
        "run_task._docker_exec", side_effect=exec_side_effect
    ):
        scores = _run_scoring("cid", require_grounded=False)

    assert not any("artifact_gate" in e for e in execs)
    assert GROUNDING_GATE_KEY not in scores


def test_unreadable_gate_output_becomes_an_infra_error():
    """Unparseable gate stdout is a broken harness, not an ungrounded answer."""

    def exec_side_effect(container_id, cmd, **kwargs):
        joined = " ".join(cmd)
        if "test.sh" in joined:
            return _exec_ok(VERIFIER_JSON)
        if "eb_verify.artifact_gate" in joined:
            m = MagicMock()
            m.returncode = 1
            m.stdout = "Traceback (most recent call last): ImportError"
            m.stderr = "ImportError: no module named eb_verify"
            return m
        return _exec_ok()

    with patch("run_task._assert_grading_assets_sealed", return_value=(True, "")), patch(
        "run_task._reap_agent_processes"
    ), patch("run_task._close_workspace_for_scoring"), patch(
        "run_task._reopen_workspace_for_agent"
    ), patch(
        "run_task._docker_exec", side_effect=exec_side_effect
    ):
        scores = _run_scoring("cid", require_grounded=True)

    assert "infra_error" in scores[GROUNDING_GATE_KEY]

    result = _result()
    _apply_grounding_gate(result, scores)
    assert result.phase == "verifier_infra_error"


def test_gate_exec_exception_does_not_sink_the_run():
    """A docker timeout in the gate must route explicitly, not raise.

    An escaped exception lands in run_task's catch-all as phase="error" and
    throws away a measurement that had already been taken.
    """
    import subprocess as _sp

    def exec_side_effect(container_id, cmd, **kwargs):
        joined = " ".join(cmd)
        if "test.sh" in joined:
            return _exec_ok(VERIFIER_JSON)
        if "eb_verify.artifact_gate" in joined:
            raise _sp.TimeoutExpired(cmd="docker", timeout=60)
        return _exec_ok()

    with patch("run_task._assert_grading_assets_sealed", return_value=(True, "")), patch(
        "run_task._reap_agent_processes"
    ), patch("run_task._close_workspace_for_scoring"), patch(
        "run_task._reopen_workspace_for_agent"
    ), patch(
        "run_task._docker_exec", side_effect=exec_side_effect
    ):
        scores = _run_scoring("cid", require_grounded=True)

    assert "infra_error" in scores[GROUNDING_GATE_KEY]


def test_gate_skipped_when_verifier_itself_infra_errored():
    """No verifier verdict means no score for the gate to force down."""

    def exec_side_effect(container_id, cmd, **kwargs):
        joined = " ".join(cmd)
        if "test.sh" in joined:
            return _exec_ok("")  # empty stdout -> guard_verifier_output InfraError
        if "eb_verify.artifact_gate" in joined:
            return _exec_ok('{"passed": false, "detail": "ungrounded"}')
        return _exec_ok()

    with patch("run_task._assert_grading_assets_sealed", return_value=(True, "")), patch(
        "run_task._reap_agent_processes"
    ), patch("run_task._close_workspace_for_scoring"), patch(
        "run_task._reopen_workspace_for_agent"
    ), patch(
        "run_task._docker_exec", side_effect=exec_side_effect
    ):
        scores = _run_scoring("cid", require_grounded=True)

    assert "verifier_infra_error" in scores
    assert GROUNDING_GATE_KEY not in scores
