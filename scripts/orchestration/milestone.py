"""
Between-session milestone verification.

Runs milestone verifiers after each session to produce intermediate scores.
Milestones are defined per-session in the chain task definition.

Scoring goes through ``eb_verify.scorer_guard`` — the same trust boundary the
Python checkpoint runner and ``test_runner.sh`` use — so all three runners share
one definition of "the verifier reached a verdict". A verifier that did not
reach one yields an :class:`InfraError`, never a number.
"""

import logging
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(REPO_ROOT / "lib"))
from eb_verify.scorer_guard import (
    InfraError,
    enforce_verdict_invariant,
    run_verifier_subprocess,
)

logger = logging.getLogger(__name__)

_STAGE = "milestone"


@dataclass
class MilestoneResult:
    """Result of running a single milestone verifier.

    ``score`` is ``None`` exactly when ``infra_error`` is set: the verifier never
    reached a verdict, so there is no score to record. Callers must not
    substitute a number for it — a placeholder 0.0 is a false zero that blames
    the agent for a broken harness, and a placeholder 1.0 is free credit.
    """

    session_number: int
    milestone_name: str
    passed: bool
    score: Optional[float]  # 0.0 to 1.0; None iff infra_error is set
    message: str = ""
    infra_error: Optional[InfraError] = None

    def __post_init__(self) -> None:
        # The aggregators sum `score` unguarded, so an ill-formed result is either a
        # crash or a laundered placeholder. Neither may be constructible. The
        # invariant has one definition, shared with CheckpointResult.
        enforce_verdict_invariant(
            f"milestone {self.milestone_name!r}", self.score, self.infra_error
        )


@dataclass
class SessionScore:
    """Aggregate score for a single session's milestones."""

    session_number: int
    milestones: list[MilestoneResult] = field(default_factory=list)

    @property
    def total_score(self) -> Optional[float]:
        """The mean milestone score, or None if any milestone never scored.

        Returning None rather than averaging over the milestones that *did* score
        is deliberate: a partial mean silently reweights the surviving milestones
        and reads downstream as a legitimate result.
        """
        if not self.milestones or any(m.infra_error for m in self.milestones):
            return None
        return sum(m.score for m in self.milestones) / len(self.milestones)

    @property
    def all_passed(self) -> bool:
        return bool(self.milestones) and all(
            m.passed and m.infra_error is None for m in self.milestones
        )


def run_milestone_verifier(
    verifier: str,
    task_dir: str,
    workspace_path: str,
    session_number: int,
    milestone_name: str,
    timeout_seconds: int = 120,
) -> MilestoneResult:
    """Run a single milestone verifier script, resolved relative to ``task_dir``.

    The verifier receives the workspace path as its first argument and MUST print
    a JSON object carrying a numeric ``score`` in [0.0, 1.0]; ``message`` is
    optional. Its exit code is never read — neither as a score nor as pass/fail.
    ``run_verifier_subprocess`` owns both.
    """
    logger.info(
        "Running milestone '%s' for session %d: %s",
        milestone_name,
        session_number,
        verifier,
    )

    verdict = run_verifier_subprocess(
        verifier,
        base_dir=Path(task_dir),
        argv_suffix=(workspace_path,),
        cwd=Path(workspace_path),
        timeout=timeout_seconds,
        checkpoint=milestone_name,
        stage=_STAGE,
    )

    if isinstance(verdict, InfraError):
        logger.error(
            "Milestone '%s' did not reach a verdict (%s): %s",
            milestone_name,
            verdict.cause,
            verdict.detail,
        )
        return MilestoneResult(
            session_number=session_number,
            milestone_name=milestone_name,
            passed=False,
            score=None,
            message=verdict.detail,
            infra_error=verdict,
        )

    return MilestoneResult(
        session_number=session_number,
        milestone_name=milestone_name,
        passed=verdict["passed"],
        score=verdict["score"],
        message=str(verdict.get("message", "")),
    )


def run_session_milestones(
    milestones: list[dict],
    workspace_path: str,
    session_number: int,
    task_dir: str,
) -> SessionScore:
    """Run all milestones for a completed session.

    Args:
        milestones: List of {"name": str, "verifier": str} dicts from task definition.
        workspace_path: Path to the workspace root.
        session_number: Which session just completed.
        task_dir: Path to the task definition directory (verifier paths are relative to this).
    """
    session_score = SessionScore(session_number=session_number)

    for milestone in milestones:
        result = run_milestone_verifier(
            verifier=milestone["verifier"],
            task_dir=task_dir,
            workspace_path=workspace_path,
            session_number=session_number,
            milestone_name=milestone["name"],
        )
        session_score.milestones.append(result)
        # An infra result is already logged, with its cause, by run_milestone_verifier.
        if result.infra_error is None:
            logger.info(
                "  Milestone '%s': %s (score=%.2f)",
                result.milestone_name,
                "PASS" if result.passed else "FAIL",
                result.score,
            )

    return session_score
