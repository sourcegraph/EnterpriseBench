"""
Weighted score computation and reward.txt generation.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import List, Optional

from eb_verify.scorer_guard import InfraError


@dataclass
class CheckpointResult:
    name: str
    weight: float
    passed: bool
    score: float  # 0.0–1.0 (partial credit possible)
    detail: str = ""
    # Set when the verifier never reached a verdict (crashed, timed out, was
    # missing, emitted no JSON). `score` is then a placeholder, NOT a
    # measurement — consumers must branch on this before reading it.
    infra_error: Optional[InfraError] = None


@dataclass
class VerificationResult:
    task_id: str
    checkpoint_results: List[CheckpointResult] = field(default_factory=list)
    artifact_results: List[dict] = field(default_factory=list)
    total_score: float = 0.0
    # Gates that forced total_score down (e.g. a required artifact failing
    # the groundedness check a task demands). Empty when no gate fired.
    score_gates: List[str] = field(default_factory=list)
    # Set when any checkpoint verifier failed to reach a verdict. Mirrors the
    # dict `run_task._run_scoring` puts on its scores payload, so both scoring
    # paths route to the same re-run channel. When set, total_score is not a
    # real measurement and must never be recorded as one.
    verifier_infra_error: Optional[dict] = None

    def summary(self) -> str:
        lines = [f"task: {self.task_id}"]
        if self.verifier_infra_error is not None:
            # Deliberately non-numeric: a downstream text parser reading
            # total_score must crash rather than quietly bank a fabricated 0.0
            # as a legitimate result.
            reason = self.verifier_infra_error.get("reason", "?")
            detail = self.verifier_infra_error.get("detail", "")
            lines.append(f"total_score: INVALID (verifier_infra_error: {reason})")
            lines.append("")
            lines.append(f"verifier_infra_error: {detail}")
        else:
            lines.append(f"total_score: {self.total_score:.4f}")
        lines.append("")

        for gate in self.score_gates:
            lines.append(f"score_gate: {gate}")
        if self.score_gates:
            lines.append("")
        lines.append("checkpoints:")
        for cr in self.checkpoint_results:
            if cr.infra_error is not None:
                # Same rule as total_score above, at the element: this
                # checkpoint's 0.0 is a placeholder, so printing it as a number
                # would hand a reader (or a per-checkpoint parser) a false zero
                # attributable to the agent.
                lines.append(
                    f"  - {cr.name}: INFRA_ERROR (no score, weight={cr.weight:.2f})"
                )
            else:
                status = "PASS" if cr.passed else "FAIL"
                lines.append(
                    f"  - {cr.name}: {status} (score={cr.score:.2f}, weight={cr.weight:.2f})"
                )
            if cr.detail:
                lines.append(f"    detail: {cr.detail}")
        if self.artifact_results:
            lines.append("")
            lines.append("artifacts:")
            for ar in self.artifact_results:
                status = "VALID" if ar.get("valid") else "INVALID"
                lines.append(f"  - {ar.get('type', '?')}: {status}")
                if ar.get("detail"):
                    lines.append(f"    detail: {ar['detail']}")
        return "\n".join(lines) + "\n"


def compute_score(results: List[CheckpointResult]) -> float:
    """Compute weighted total from checkpoint results. Weights should sum to 1.0."""
    if not results:
        return 0.0
    total_weight = sum(r.weight for r in results)
    if total_weight == 0.0:
        return 0.0
    raw = sum(r.score * r.weight for r in results)
    return raw / total_weight


def write_reward(result: VerificationResult, output_path: str | Path = "reward.txt") -> Path:
    """Write reward.txt with per-checkpoint and total scores."""
    output_path = Path(output_path)
    output_path.write_text(result.summary())
    return output_path
