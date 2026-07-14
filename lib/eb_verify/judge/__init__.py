"""LLM judge for EnterpriseBench Tier 2 verification.

Ported from CodeScaleBench's csb_metrics/judge/ and adapted for EB's
per-checkpoint verification model. Uses LLM-as-judge with curated ground
truth to replace brittle grep-based verifiers for semantic checkpoints.

Usage:
    from eb_verify.judge import LLMJudge, CheckpointJudgeInput

    judge = LLMJudge(model="claude-haiku-4-5-20251001")
    result = judge.evaluate_checkpoint(CheckpointJudgeInput(
        task_id="incident-inv-004",
        checkpoint_name="root_cause",
        agent_output="...",
        expected_solution="...",
        evaluation_criteria=["Must identify monitor.go", ...],
    ))
    print(result.score, result.passed)
"""

from eb_verify.judge.backends import JudgeBackendError
from eb_verify.judge.engine import LLMJudge
from eb_verify.judge.models import (
    CheckpointJudgeInput,
    CheckpointJudgeResult,
    JudgeScoreError,
)

__all__ = [
    "LLMJudge",
    "CheckpointJudgeInput",
    "CheckpointJudgeResult",
    "JudgeBackendError",
    "JudgeScoreError",
]
