"""
Weighted score computation and reward.txt generation.

Also home of :func:`score_file_set` / :class:`FileSetScore`, the benchmark-wide
scoring primitive for file-discovery checkpoints (bead EnterpriseBench-vdeyx).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, List, Optional, Sequence

from eb_verify.scorer_guard import InfraError

# Inclusive: a file-set checkpoint passes at F1 >= this. Kept at the historical
# recall-only pass bar so "passed" keeps meaning "half-right or better".
FILE_SET_PASS_THRESHOLD = 0.5


@dataclass(frozen=True)
class FileSetScore:
    """Set-F1 verdict over a file-discovery answer.

    Every file-discovery checkpoint scores through this one primitive so the 28
    checkpoints stay comparable (bead vdeyx settles the methodology benchmark-
    wide). It replaces recall-only scoring, where listing extra files cost
    nothing and enumerating every plausible file in the repo near-guaranteed
    full credit without the investigation the checkpoint exists to measure.

    Holds the four counts; precision/recall/F1 are derived. Counts, not paths,
    because not every matcher is a pairwise predicate — file_extraction.py's
    refinement rule credits per answer and can leave a guess ambiguous, so that
    call site constructs from counts while blob-style call sites go through
    :func:`score_file_set`.
    """

    n_required: int  # distinct ground-truth files
    n_matched: int  # ground-truth files the answer identified
    n_listed: int  # distinct files the agent listed
    n_relevant: int  # listed files credited against >= 1 ground-truth file

    def __post_init__(self):
        if min(self.n_required, self.n_matched, self.n_listed, self.n_relevant) < 0:
            raise ValueError(f"negative count in {self}")
        if self.n_matched > self.n_required:
            raise ValueError(f"matched {self.n_matched} > required {self.n_required}")
        if self.n_relevant > self.n_listed:
            raise ValueError(f"relevant {self.n_relevant} > listed {self.n_listed}")

    @property
    def recall(self) -> float:
        """Share of required files identified; 1.0 when nothing was required."""
        if self.n_required == 0:
            return 1.0
        return self.n_matched / self.n_required

    @property
    def precision(self) -> float:
        """Share of listed files that were relevant; 1.0 when nothing was
        listed (no false positives — an empty answer is punished by recall)."""
        if self.n_listed == 0:
            return 1.0
        return self.n_relevant / self.n_listed

    @property
    def f1(self) -> float:
        """Harmonic mean of precision and recall; 0.0 when both are 0.

        At the pass threshold this subsumes the "cap |answer| at 3x |GT|"
        alternative: with full recall, F1 >= 0.5 iff n_listed <= 3*n_required —
        but continuously, with no arbitrary constant, and still charging for
        the enumeration a bare cap would wave through.
        """
        p, r = self.precision, self.recall
        if p + r == 0:
            return 0.0
        return 2 * p * r / (p + r)

    @property
    def passed(self) -> bool:
        return self.f1 >= FILE_SET_PASS_THRESHOLD

    def detail(self) -> str:
        """Human-readable verdict: found/total, listed count, P/R/F1."""
        return (
            f"{self.n_matched}/{self.n_required} required files found, "
            f"{self.n_listed} listed; "
            f"P={self.precision:.2f} R={self.recall:.2f} F1={self.f1:.2f}"
        )


def score_file_set(
    agent_files: Sequence[str],
    required_files: Sequence[str],
    match: Callable[[str, str], bool] | None = None,
) -> FileSetScore:
    """Score a file-discovery answer against required files with set-F1.

    ``match(agent_path, required_path)`` decides whether one listed file names
    one required file; default is exact string equality. The matcher owns all
    path semantics — normalization, suffix policy, component boundaries — and
    this function owns the scoring math, so matcher changes (e.g. ssikq's
    component-suffix rule) never change what over-listing costs.

    Both lists are deduplicated by exact string first: repeating the right
    answer is not spray, and repeating filler earns no extra penalty. Distinct
    spellings of one file are the matcher's/call site's problem, not this
    function's.
    """
    if match is None:
        match = lambda agent, required: agent == required  # noqa: E731
    required = list(dict.fromkeys(required_files))
    listed = list(dict.fromkeys(agent_files))
    n_matched = sum(1 for gt in required if any(match(af, gt) for af in listed))
    n_relevant = sum(1 for af in listed if any(match(af, gt) for gt in required))
    return FileSetScore(
        n_required=len(required),
        n_matched=n_matched,
        n_listed=len(listed),
        n_relevant=n_relevant,
    )


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
