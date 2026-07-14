"""Data models for the EB LLM Judge."""

from __future__ import annotations

from dataclasses import dataclass, field

from eb_verify.scorer_guard import is_valid_score


class JudgeScoreError(Exception):
    """The judge returned something that is not a score.

    Not a zero and not full marks — no verdict at all. The caller must route it
    to the re-run channel rather than record a number the judge never gave.
    """


@dataclass
class CheckpointJudgeInput:
    """Input for evaluating a single checkpoint via LLM judge."""

    task_id: str
    checkpoint_name: str
    agent_output: str  # full agent answer (answer.json or INCIDENT_REPORT.md)
    expected_solution: str  # curated ground truth for this checkpoint
    evaluation_criteria: list[str] = field(default_factory=list)
    checkpoint_weight: float = 1.0


@dataclass
class CheckpointJudgeResult:
    """Output from LLM judge for a single checkpoint."""

    checkpoint_name: str
    score: float  # 0.0 - 1.0
    passed: bool  # score >= pass_threshold
    reasoning: str = ""
    evidence: str = ""  # specific quotes from agent output that matched/missed
    confidence: str = "medium"  # high | medium | low
    model: str = ""
    raw_response: dict = field(default_factory=dict)


def validate_score(val: object) -> float:
    """Return the judge's score, or raise :class:`JudgeScoreError`.

    A value that is not a score never becomes one — see
    :func:`eb_verify.scorer_guard.is_valid_score` for what that rules out.
    """
    if not is_valid_score(val):
        raise JudgeScoreError(f"judge returned a non-score: {val!r}")
    # Already in range; this only trims the bound slop is_valid_score tolerates.
    return min(1.0, max(0.0, float(val)))  # type: ignore[arg-type]
