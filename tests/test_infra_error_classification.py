"""Tests for infra error classification in run_task.py.

Verifies that specific agent exit codes produce correct failure_class values:
  exit 137 (OOM/SIGKILL) → failure_class='infra_oom', phase='agent_infra_error'
  exit 124 (timeout)      → failure_class='infra_timeout', phase='agent_infra_error'
  exit 1   (normal fail)  → failure_class='agent_error', phase unchanged
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

# Make scripts importable
sys.path.insert(
    0, str(Path(__file__).resolve().parent.parent / "scripts" / "orchestration")
)
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts" / "infra"))

from run_task import TaskRunConfig, TaskRunResult


def _make_config(**overrides) -> TaskRunConfig:
    defaults = dict(
        task_toml=Path("/fake/task.toml"),
        source="mirror",
        agent_command="claude -p",
        timeout=300,
        build_timeout=600,
        verifier_timeout=120,
        memory_mb=8192,
        output_dir=None,
        dry_run=False,
        no_build=False,
        keep_container=False,
        verbose=False,
        account=None,
        mode="baseline",
    )
    defaults.update(overrides)
    return TaskRunConfig(**defaults)


# ---------------------------------------------------------------------------
# Unit tests: verify exit-code → failure_class mapping logic directly
# ---------------------------------------------------------------------------


class TestInfraErrorClassification:
    """Test that agent exit codes map to correct failure_class and phase."""

    def _simulate_agent_exit(self, exit_code: int) -> TaskRunResult:
        """Simulate the agent exit code handling logic from run_task.py."""
        result = TaskRunResult(task_id="test-infra-001")

        # Replicate the exact branching logic from run_task.py
        if exit_code == 137:
            result.failure_class = "infra_oom"
            result.phase = "agent_infra_error"
        elif exit_code == 124:
            result.failure_class = "infra_timeout"
            result.phase = "agent_infra_error"
        elif exit_code != 0:
            result.failure_class = "agent_error"

        # Replicate the phase-complete guard
        if result.phase != "agent_infra_error":
            result.phase = "complete"
            result.success = True

        return result

    def test_exit_137_produces_infra_oom(self) -> None:
        result = self._simulate_agent_exit(137)
        assert result.failure_class == "infra_oom"
        assert result.phase == "agent_infra_error"
        assert result.phase != "complete"
        assert result.success is False

    def test_exit_124_produces_infra_timeout(self) -> None:
        result = self._simulate_agent_exit(124)
        assert result.failure_class == "infra_timeout"
        assert result.phase == "agent_infra_error"
        assert result.phase != "complete"
        assert result.success is False

    def test_exit_1_produces_agent_error(self) -> None:
        result = self._simulate_agent_exit(1)
        assert result.failure_class == "agent_error"
        assert result.phase == "complete"
        assert result.success is True

    def test_exit_0_no_failure_class(self) -> None:
        result = self._simulate_agent_exit(0)
        assert result.failure_class is None
        assert result.phase == "complete"
        assert result.success is True

    def test_exit_2_produces_agent_error(self) -> None:
        """Other non-zero exits should still be agent_error."""
        result = self._simulate_agent_exit(2)
        assert result.failure_class == "agent_error"

    def test_infra_oom_success_is_false(self) -> None:
        result = self._simulate_agent_exit(137)
        assert result.success is False

    def test_infra_timeout_success_is_false(self) -> None:
        result = self._simulate_agent_exit(124)
        assert result.success is False


# ---------------------------------------------------------------------------
# Source-level verification: confirm run_task.py contains the expected logic
# ---------------------------------------------------------------------------


class TestRunTaskSourceLogic:
    """Verify that run_task.py source code contains the infra error handling."""

    @pytest.fixture(autouse=True)
    def _read_source(self) -> None:
        src = (
            Path(__file__).resolve().parent.parent
            / "scripts"
            / "orchestration"
            / "run_task.py"
        )
        self.source = src.read_text()

    def test_exit_137_handled(self) -> None:
        assert "agent_exit == 137" in self.source
        assert '"infra_oom"' in self.source

    def test_exit_124_handled(self) -> None:
        assert "agent_exit == 124" in self.source
        assert '"infra_timeout"' in self.source

    def test_agent_infra_error_phase(self) -> None:
        assert '"agent_infra_error"' in self.source

    def test_phase_guard_prevents_complete_override(self) -> None:
        # The success/complete override must be skipped for any infra-error
        # phase (agent- or verifier-side), so infra errors route to re-run —
        # and for a grading-asset integrity violation, which must never be
        # recorded as a completed run (bead EnterpriseBench-8krz5).
        from run_task import NON_COMPLETE_PHASES

        assert NON_COMPLETE_PHASES == {
            "agent_infra_error",
            "verifier_infra_error",
            "integrity_violation",
        }

    def test_judge_skipped_when_scores_are_untrusted(self) -> None:
        # The judge must not run against scores the deterministic stage already
        # flagged: there is nothing trustworthy left to put a ceiling on. An
        # agent-side infra error is scoreable-but-failed, so it is NOT in this set.
        from run_task import NON_COMPLETE_PHASES, UNTRUSTED_SCORE_PHASES

        assert UNTRUSTED_SCORE_PHASES == {
            "verifier_infra_error",
            "integrity_violation",
        }
        assert UNTRUSTED_SCORE_PHASES < NON_COMPLETE_PHASES
