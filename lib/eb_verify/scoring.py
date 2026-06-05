"""
Weighted score computation, reward.txt generation, and unified ScoreResult emission.

The unified ScoreResult contract is the cross-benchmark JSON schema shared by
EnterpriseBench and CSB. It carries:

* ``reward`` (float): the weighted-average checkpoint score, identical to the
  legacy total_score.
* ``scorer_family`` (str): always ``"checklist"`` for EB (each checkpoint is
  a distinct yes/no/partial item; the rubric is the family).
* ``sub_scores`` (dict): each checkpoint's score on [0, 1], keyed by name.
* ``diagnostics`` (dict):
    - ``task_time_seconds``: optional float, plumbed from the harness.
    - ``token_cost_usd``: optional float, plumbed from the harness.
    - ``input_tokens`` / ``output_tokens``: optional ints, plumbed from the
      harness. Raw token counts (summed across the multi-turn conversation),
      mirroring codeprobe so cost-Pareto plots that aren't dollar-locked can
      compare models with different per-token pricing.
    - ``ir_metrics``: always ``None`` for EB (information-retrieval style
      metrics aren't measured here).
    - ``artifact_results``: dict keyed by artifact type, with ``valid`` and
      ``detail`` per entry.

Artifact validity is reported separately from reward because validity is a
contract concern (did the agent produce a parseable artifact at all?) while
reward is a quality concern (how well did the artifact answer the task?).
"""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any


SCORER_FAMILY_CHECKLIST = "checklist"


@dataclass
class CheckpointResult:
    name: str
    weight: float
    passed: bool
    score: float  # 0.0–1.0 (partial credit possible)
    detail: str = ""


@dataclass(frozen=True)
class ScoreDiagnostics:
    """Optional sidecar fields plumbed from the harness layer.

    Each field defaults to ``None`` so callers that don't have measurements
    yet can still emit a ScoreResult. Mutating callers should construct a
    new ``ScoreDiagnostics`` rather than re-assigning fields.
    """

    task_time_seconds: float | None = None
    token_cost_usd: float | None = None
    input_tokens: int | None = None
    output_tokens: int | None = None
    ir_metrics: dict[str, Any] | None = None
    artifact_results: dict[str, dict[str, Any]] = field(default_factory=dict)


@dataclass(frozen=True)
class ScoreResult:
    """Cross-benchmark unified scoring contract.

    See module docstring for the full schema. The dataclass is frozen so
    consumers can hash, compare, and serialise without worrying about
    silent mutation. Use :meth:`to_dict` for the canonical wire format.
    """

    task_id: str
    reward: float
    scorer_family: str = SCORER_FAMILY_CHECKLIST
    sub_scores: dict[str, float] = field(default_factory=dict)
    diagnostics: ScoreDiagnostics = field(default_factory=ScoreDiagnostics)

    def to_dict(self) -> dict[str, Any]:
        """Return the canonical JSON-serialisable wire shape.

        ``task_id`` is included so callers writing many results to one file
        don't need to track the key separately.
        """
        return {
            "task_id": self.task_id,
            "reward": self.reward,
            "scorer_family": self.scorer_family,
            "sub_scores": dict(self.sub_scores),
            "diagnostics": asdict(self.diagnostics),
        }


@dataclass
class VerificationResult:
    task_id: str
    checkpoint_results: list[CheckpointResult] = field(default_factory=list)
    artifact_results: list[dict] = field(default_factory=list)
    total_score: float = 0.0

    def summary(self) -> str:
        lines = [f"task: {self.task_id}", f"total_score: {self.total_score:.4f}", ""]
        lines.append("checkpoints:")
        for cr in self.checkpoint_results:
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

    def to_score_result(
        self,
        *,
        task_time_seconds: float | None = None,
        token_cost_usd: float | None = None,
        input_tokens: int | None = None,
        output_tokens: int | None = None,
    ) -> ScoreResult:
        """Project this VerificationResult into the unified ScoreResult.

        Args:
            task_time_seconds: Optional wall-clock duration plumbed from the
                harness. The legacy harness records this in ``analyze_scores.py``
                and ``reward.txt`` separately; passing it through here
                consolidates the surface.
            token_cost_usd: Optional per-task token cost in USD, also
                plumbed from the harness.
            input_tokens: Optional raw input-token count (summed across the
                conversation), plumbed from the harness for cost-Pareto plots.
            output_tokens: Optional raw output-token count, likewise plumbed.
        """
        sub_scores = {
            cr.name: max(0.0, min(1.0, cr.score)) for cr in self.checkpoint_results
        }
        artifact_results = {
            ar.get("type", "?"): {
                "valid": bool(ar.get("valid")),
                "detail": ar.get("detail", ""),
            }
            for ar in self.artifact_results
        }
        return ScoreResult(
            task_id=self.task_id,
            reward=float(self.total_score),
            scorer_family=SCORER_FAMILY_CHECKLIST,
            sub_scores=sub_scores,
            diagnostics=ScoreDiagnostics(
                task_time_seconds=task_time_seconds,
                token_cost_usd=token_cost_usd,
                input_tokens=input_tokens,
                output_tokens=output_tokens,
                ir_metrics=None,
                artifact_results=artifact_results,
            ),
        )


def compute_score(results: list[CheckpointResult]) -> float:
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


def write_score_result(
    result: VerificationResult,
    output_path: str | Path = "score_result.json",
    *,
    task_time_seconds: float | None = None,
    token_cost_usd: float | None = None,
    input_tokens: int | None = None,
    output_tokens: int | None = None,
) -> Path:
    """Write the unified ScoreResult JSON next to ``reward.txt``.

    The two files are deliberately separate: ``reward.txt`` is a
    human-readable EB-internal summary; ``score_result.json`` is the
    cross-benchmark wire format consumed by aggregators. Keeping both
    avoids breaking existing tooling that scrapes ``reward.txt``.
    """
    output_path = Path(output_path)
    score = result.to_score_result(
        task_time_seconds=task_time_seconds,
        token_cost_usd=token_cost_usd,
        input_tokens=input_tokens,
        output_tokens=output_tokens,
    )
    output_path.write_text(json.dumps(score.to_dict(), indent=2) + "\n")
    return output_path
