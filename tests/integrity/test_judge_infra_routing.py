"""Judge-outage vectors (apfp #3, over-credit direction).

A malformed ground truth, a judge-init failure, or a per-checkpoint judge
exception must NOT leave the un-capped deterministic grep score standing as the
final measurement. Each must tag scores['verifier_infra_error'] so run_task
routes the run to re-run instead of recording an inflated score.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(REPO_ROOT / "scripts" / "orchestration"))
sys.path.insert(0, str(REPO_ROOT / "scripts" / "infra"))

import run_task  # noqa: E402
from run_task import (  # noqa: E402
    TaskRunResult,
    _apply_llm_judge,
    _route_verifier_infra_error,
)


def _write_expected(task_dir: Path, body: str) -> None:
    (task_dir / "expected_solution.json").write_text(body)


def _valid_expected() -> str:
    return json.dumps(
        {
            "task_id": "t1",
            "checkpoints": {
                "cp1": {
                    "expected_solution": "the answer is X",
                    "evaluation_criteria": ["mentions X"],
                }
            },
        }
    )


def _uncapped_scores() -> dict:
    """A deterministic grep score that OVER-credits (grep said 1.0)."""
    return {
        "task_score": 1.0,
        "checkpoints": [{"name": "cp1", "score": 1.0, "passed": True, "weight": 1.0}],
    }


def _agent_output_present():
    """Patch _docker_exec so the agent artifact `cat` succeeds."""
    ok = MagicMock(returncode=0, stdout='{"answer": "X"}', stderr="")
    return patch.object(run_task, "_docker_exec", return_value=ok)


def _judge_whose_backend(*, returns: dict | None = None, raises: Exception | None = None):
    """A real LLMJudge wired to a mock backend, bypassing backend construction."""
    sys.path.insert(0, str(REPO_ROOT / "lib"))
    from eb_verify.judge import LLMJudge

    judge = LLMJudge.__new__(LLMJudge)
    judge.model = "cc:haiku"
    judge.pass_threshold = 0.5
    judge._backend = MagicMock()
    judge._backend.call.return_value = returns
    judge._backend.call.side_effect = raises
    return judge


class TestJudgeOutageRouting:
    def test_malformed_expected_solution_flags_infra(self, tmp_path: Path) -> None:
        _write_expected(tmp_path, "{ this is not valid json ]")
        scores = _apply_llm_judge(_uncapped_scores(), tmp_path, "cid", {})
        err = scores.get("verifier_infra_error")
        assert err is not None, "malformed GT must not pass through un-capped grep"
        assert err["reason"] == "malformed_expected_solution"

    def test_judge_init_failure_flags_infra(self, tmp_path: Path) -> None:
        _write_expected(tmp_path, _valid_expected())
        # Judge import/construction raises → cap cannot be applied.
        with _agent_output_present(), patch(
            "eb_verify.judge.LLMJudge", side_effect=RuntimeError("no credentials")
        ):
            scores = _apply_llm_judge(_uncapped_scores(), tmp_path, "cid", {})
        err = scores.get("verifier_infra_error")
        assert err is not None, "judge-init failure must not pass through un-capped grep"
        assert err["reason"] == "judge_init_failed"
        # The un-capped grep score must NOT be recorded as a clean final score.
        assert scores["task_score"] == 1.0  # unchanged, but flagged infra → re-run

    def test_per_checkpoint_judge_exception_flags_infra(self, tmp_path: Path) -> None:
        _write_expected(tmp_path, _valid_expected())
        judge = MagicMock()
        judge.evaluate_checkpoint.side_effect = RuntimeError("judge 500")
        with _agent_output_present(), patch(
            "eb_verify.judge.LLMJudge", return_value=judge
        ):
            scores = _apply_llm_judge(_uncapped_scores(), tmp_path, "cid", {})
        err = scores.get("verifier_infra_error")
        assert err is not None, "per-checkpoint judge exception must not keep un-capped grep"
        assert err["reason"] == "judge_checkpoint_failed"

    def test_judge_returning_a_non_score_flags_infra(self, tmp_path: Path) -> None:
        """A NaN judge score is not a verdict. A clamp would make it a free 1.0,
        turning the Tier-2 cap into a no-op and leaving the un-capped grep 1.0
        standing as the final measurement."""
        _write_expected(tmp_path, _valid_expected())
        judge = _judge_whose_backend(returns={"score": float("nan")})

        with _agent_output_present(), patch(
            "eb_verify.judge.LLMJudge", return_value=judge
        ):
            scores = _apply_llm_judge(_uncapped_scores(), tmp_path, "cid", {})

        err = scores.get("verifier_infra_error")
        assert err is not None, "a NaN judge score must not become a free 1.0"
        assert err["reason"] == "judge_checkpoint_failed"
        assert "non-score" in err["detail"]

    def test_judge_backend_outage_flags_infra_not_zero(self, tmp_path: Path) -> None:
        """A judge OUTAGE is our infra failing, not the agent. Scoring it 0.0
        would propagate through min(grep, judge) to every checkpoint."""
        _write_expected(tmp_path, _valid_expected())
        from eb_verify.judge import JudgeBackendError

        judge = _judge_whose_backend(raises=JudgeBackendError("judge 503"))

        with _agent_output_present(), patch(
            "eb_verify.judge.LLMJudge", return_value=judge
        ):
            scores = _apply_llm_judge(_uncapped_scores(), tmp_path, "cid", {})

        err = scores.get("verifier_infra_error")
        assert err is not None, "a judge outage must not be scored 0.0"
        assert err["reason"] == "judge_checkpoint_failed"
        assert scores["checkpoints"][0]["score"] == 1.0, (
            "the outage must not have overwritten the checkpoint with a false zero; "
            "the run is flagged for re-run, not scored"
        )

    def test_healthy_judge_caps_and_does_not_flag(self, tmp_path: Path) -> None:
        """Negative control: a healthy judge applies min(grep, judge) and does
        NOT raise a spurious infra error."""
        _write_expected(tmp_path, _valid_expected())
        judge = MagicMock()
        judge.evaluate_checkpoint.return_value = MagicMock(score=0.0, reasoning="wrong")
        with _agent_output_present(), patch(
            "eb_verify.judge.LLMJudge", return_value=judge
        ):
            scores = _apply_llm_judge(_uncapped_scores(), tmp_path, "cid", {})
        assert "verifier_infra_error" not in scores
        assert scores["task_score"] == 0.0  # grep 1.0 capped to judge 0.0


class TestRouteHelper:
    def test_route_sets_phase_and_failure_class(self) -> None:
        result = TaskRunResult(task_id="t")
        scores = {
            "task_score": 1.0,
            "verifier_infra_error": {"reason": "judge_init_failed", "stage": "llm_judge", "detail": "x"},
        }
        _route_verifier_infra_error(result, scores)
        assert result.phase == "verifier_infra_error"
        assert result.failure_class == "verifier_infra_error"

    def test_route_noop_when_clean(self) -> None:
        result = TaskRunResult(task_id="t")
        _route_verifier_infra_error(result, {"task_score": 1.0})
        assert result.phase != "verifier_infra_error"
        assert result.failure_class is None

    def test_infra_phase_blocks_complete_success(self) -> None:
        """The phase-complete guard must never mark an infra-flagged run
        complete/success (mirrors run_task's save gate)."""
        result = TaskRunResult(task_id="t")
        _route_verifier_infra_error(
            result,
            {"verifier_infra_error": {"reason": "r", "stage": "s", "detail": "d"}},
        )
        # Replicate run_task's save gate:
        if result.phase not in ("agent_infra_error", "verifier_infra_error"):
            result.phase = "complete"
            result.success = True
        assert result.phase == "verifier_infra_error"
        assert result.success is False
