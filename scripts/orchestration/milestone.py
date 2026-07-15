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
from eb_verify.scorer_guard import InfraError, run_verifier_subprocess

logger = logging.getLogger(__name__)

_STAGE = "milestone"


@dataclass
class MilestoneResult:
    """Result of running a single milestone verifier.

    ``score`` is ``None`` exactly when ``infra_error`` is set: the verifier never
    reached a verdict, so there is no score to record. Callers must not
    substitute a number for it — a placeholder 0.0 is a false zero that blames
    the agent for a broken harness, and a placeholder 1.0 is free credit.

    ``not_reached`` marks the one 0.0 this rule permits without a verifier having
    run: the chain ended in an agent failure before this milestone's session, so
    the work it would measure was never done (bead jma9y). It is scored rather
    than dropped because dropping it would shrink the denominator and reweight the
    milestones the agent did pass. The flag is what keeps it from being the very
    placeholder the paragraph above forbids — a reader can tell this zero from a
    verifier's real one, and check the claim behind it.
    """

    session_number: int
    milestone_name: str
    passed: bool
    score: Optional[float]  # 0.0 to 1.0; None iff infra_error is set
    message: str = ""
    infra_error: Optional[InfraError] = None
    not_reached: bool = False

    def __post_init__(self) -> None:
        # The aggregators sum `score` unguarded, so an ill-formed result is either a
        # crash or a laundered placeholder. Neither may be constructible.
        if (self.score is None) != (self.infra_error is not None):
            raise ValueError(
                f"milestone {self.milestone_name!r}: exactly one of score/infra_error "
                f"must be set — a verifier either reached a verdict or it did not "
                f"(score={self.score!r}, infra_error={self.infra_error!r})"
            )
        # An unreached milestone is the one scored 0.0 without a verifier; it may
        # not be anything else. Left unguarded, the flag becomes a way to launder
        # an arbitrary number past the rule it is an exception to. `passed` is
        # checked too: a milestone the agent never reached cannot have been passed,
        # and `all_passed` reads that field without consulting the score.
        if self.not_reached and (
            self.score != 0.0 or self.infra_error is not None or self.passed
        ):
            raise ValueError(
                f"milestone {self.milestone_name!r}: not_reached means the agent "
                f"never got here, which is a failed 0.0 and nothing else "
                f"(score={self.score!r}, passed={self.passed!r}, "
                f"infra_error={self.infra_error!r})"
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
