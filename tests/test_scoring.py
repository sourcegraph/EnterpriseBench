"""Tests for eb_verify.scoring."""

from __future__ import annotations

from pathlib import Path

import pytest

from eb_verify.scorer_guard import NO_VERDICT_REASON, InfraError
from eb_verify.scoring import (
    CheckpointResult,
    VerificationResult,
    compute_score,
    write_reward,
)


def make_result(name: str, weight: float, passed: bool, score: float, detail: str = "") -> CheckpointResult:
    return CheckpointResult(name=name, weight=weight, passed=passed, score=score, detail=detail)


def make_infra_error() -> InfraError:
    return InfraError(NO_VERDICT_REASON, "deterministic_scoring", "boom")


# ---------------------------------------------------------------------------
# compute_score
# ---------------------------------------------------------------------------

class TestComputeScore:
    def test_all_pass(self):
        results = [
            make_result("a", 0.5, True, 1.0),
            make_result("b", 0.5, True, 1.0),
        ]
        assert compute_score(results) == pytest.approx(1.0)

    def test_all_fail(self):
        results = [
            make_result("a", 0.5, False, 0.0),
            make_result("b", 0.5, False, 0.0),
        ]
        assert compute_score(results) == pytest.approx(0.0)

    def test_partial_credit(self):
        results = [
            make_result("a", 0.6, True, 1.0),
            make_result("b", 0.4, False, 0.0),
        ]
        assert compute_score(results) == pytest.approx(0.6)

    def test_partial_score_on_checkpoint(self):
        results = [
            make_result("a", 0.5, True, 0.5),
            make_result("b", 0.5, True, 1.0),
        ]
        assert compute_score(results) == pytest.approx(0.75)

    def test_empty_list(self):
        assert compute_score([]) == 0.0

    def test_single_checkpoint_full_weight(self):
        results = [make_result("only", 1.0, True, 1.0)]
        assert compute_score(results) == pytest.approx(1.0)

    def test_single_checkpoint_fail(self):
        results = [make_result("only", 1.0, False, 0.0)]
        assert compute_score(results) == pytest.approx(0.0)

    @pytest.mark.parametrize("w1,w2,s1,s2,expected", [
        (0.3, 0.7, 1.0, 0.5, 0.3 * 1.0 + 0.7 * 0.5),
        (0.2, 0.8, 0.0, 1.0, 0.8),
        (0.5, 0.5, 0.8, 0.4, 0.5 * 0.8 + 0.5 * 0.4),
    ])
    def test_parametrized_weights(self, w1, w2, s1, s2, expected):
        results = [
            make_result("a", w1, s1 > 0, s1),
            make_result("b", w2, s2 > 0, s2),
        ]
        assert compute_score(results) == pytest.approx(expected)


# ---------------------------------------------------------------------------
# The no-verdict encoding
# ---------------------------------------------------------------------------

class TestNoVerdictEncoding:
    """score and infra_error are mutually exclusive and jointly exhaustive.

    The ill-formed states are unconstructible, not merely documented — a
    placeholder 0.0 alongside an infra_error is a false zero that blames the
    agent for a broken harness.
    """

    def test_placeholder_score_with_infra_error_is_unconstructible(self):
        with pytest.raises(ValueError, match="reached a verdict"):
            CheckpointResult(
                name="cp",
                weight=1.0,
                passed=False,
                score=0.0,
                infra_error=make_infra_error(),
            )

    def test_missing_score_without_infra_error_is_unconstructible(self):
        with pytest.raises(ValueError, match="reached a verdict"):
            CheckpointResult(name="cp", weight=1.0, passed=False, score=None)

    def test_infra_checkpoint_carries_no_score(self):
        result = CheckpointResult(
            name="cp",
            weight=1.0,
            passed=False,
            score=None,
            infra_error=make_infra_error(),
        )
        assert result.score is None
        assert result.infra_error is not None

    def test_compute_score_refuses_unscored_checkpoints(self):
        """Averaging around a missing verdict fabricates a plausible total —
        the aggregate must crash naming the checkpoint, not invent a number."""
        results = [
            make_result("scored", 0.5, True, 1.0),
            CheckpointResult(
                name="broken",
                weight=0.5,
                passed=False,
                score=None,
                infra_error=make_infra_error(),
            ),
        ]
        with pytest.raises(ValueError, match="broken"):
            compute_score(results)


# ---------------------------------------------------------------------------
# VerificationResult.summary()
# ---------------------------------------------------------------------------

class TestVerificationResultSummary:
    def test_summary_contains_task_id(self):
        vr = VerificationResult(task_id="my-task-001")
        summary = vr.summary()
        assert "my-task-001" in summary

    def test_summary_contains_total_score(self):
        results = [make_result("step", 1.0, True, 1.0)]
        vr = VerificationResult(
            task_id="t",
            checkpoint_results=results,
            total_score=1.0,
        )
        assert "1.0000" in vr.summary()

    def test_summary_pass_fail_labels(self):
        results = [
            make_result("pass_step", 0.5, True, 1.0),
            make_result("fail_step", 0.5, False, 0.0),
        ]
        vr = VerificationResult(task_id="t", checkpoint_results=results, total_score=0.5)
        summary = vr.summary()
        assert "PASS" in summary
        assert "FAIL" in summary

    def test_summary_includes_detail(self):
        results = [make_result("step", 1.0, True, 1.0, detail="looks good")]
        vr = VerificationResult(task_id="t", checkpoint_results=results, total_score=1.0)
        assert "looks good" in vr.summary()

    def test_summary_artifact_results(self):
        vr = VerificationResult(
            task_id="t",
            artifact_results=[{"type": "code_patch", "valid": True, "detail": "ok"}],
            total_score=0.0,
        )
        summary = vr.summary()
        assert "VALID" in summary
        assert "code_patch" in summary

    def test_summary_artifact_invalid(self):
        vr = VerificationResult(
            task_id="t",
            artifact_results=[{"type": "config", "valid": False, "detail": "bad yaml"}],
            total_score=0.0,
        )
        assert "INVALID" in vr.summary()
        assert "bad yaml" in vr.summary()

    def test_summary_no_artifacts_section_when_empty(self):
        vr = VerificationResult(task_id="t", total_score=0.0)
        summary = vr.summary()
        # Should still have checkpoint section header
        assert "checkpoints:" in summary


# ---------------------------------------------------------------------------
# write_reward
# ---------------------------------------------------------------------------

class TestWriteReward:
    def test_creates_file(self, tmp_path):
        vr = VerificationResult(task_id="t", total_score=0.5)
        out = tmp_path / "reward.txt"
        result = write_reward(vr, out)
        assert result == out
        assert out.exists()

    def test_file_contents_match_summary(self, tmp_path):
        results = [make_result("step", 1.0, True, 1.0)]
        vr = VerificationResult(task_id="wt", checkpoint_results=results, total_score=1.0)
        out = tmp_path / "reward.txt"
        write_reward(vr, out)
        assert out.read_text() == vr.summary()

    def test_returns_path_object(self, tmp_path):
        vr = VerificationResult(task_id="t", total_score=0.0)
        result = write_reward(vr, str(tmp_path / "reward.txt"))
        assert isinstance(result, Path)
