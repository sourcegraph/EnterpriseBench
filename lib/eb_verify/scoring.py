"""
Weighted score computation and reward.txt generation.

Also home of :func:`score_file_set` / :class:`FileSetScore`, the benchmark-wide
scoring primitive for file-discovery checkpoints (bead EnterpriseBench-vdeyx).
"""

from __future__ import annotations

import operator
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
    checkpoints stay comparable; it replaces recall-only scoring, which charged
    nothing for over-listing. Rationale and options considered:
    docs/file_discovery_scoring_decision.md.

    Three counts; precision/recall/F1 are derived. ``n_matched`` is the single
    true-positive count shared by both — deliberately ONE number, because two
    independently-counted true-positive totals (one per side) is what let both
    halves of the answer lie: a vague guess crediting several required files at
    once inflated recall, and many near-duplicate spellings of one found file
    all counting as "relevant" inflated precision. Both are ruled out by
    construction here: ``n_matched`` is a 1-to-1 pairing, so it can never exceed
    either side's cardinality.

    Counts, not paths, because not every matcher is a pairwise predicate —
    file_extraction.py's refinement rule credits per answer, so that call site
    constructs from counts while blob-style call sites go through
    :func:`score_file_set`. Count-constructing callers owe the contract
    :func:`score_file_set` enforces for them: counts are over DISTINCT files,
    and ``n_matched`` is a 1-to-1 pairing (an ambiguous guess is listed, but
    credits nothing).
    """

    n_required: int  # distinct ground-truth files; >= 1
    n_listed: int  # distinct files the agent listed
    n_matched: int  # required files credited, under a 1-to-1 pairing

    def __post_init__(self) -> None:
        if min(self.n_listed, self.n_matched) < 0:
            raise ValueError(f"negative count in {self}")
        if self.n_required < 1:
            # A checkpoint with nothing to find is a task-authoring bug. Scoring
            # it vacuously perfect would hand every agent a free pass, so refuse
            # to produce a number at all and let the caller raise an infra error.
            raise ValueError(f"no required files to score against in {self}")
        if self.n_matched > min(self.n_required, self.n_listed):
            # Unreachable from any real pairing: you cannot credit more files
            # than either side holds. Catches miscounts at the counts-based
            # call site, which would otherwise fabricate a passing score.
            raise ValueError(
                f"matched {self.n_matched} exceeds required {self.n_required} "
                f"or listed {self.n_listed}"
            )

    @property
    def recall(self) -> float:
        """Share of required files found."""
        return self.n_matched / self.n_required

    @property
    def precision(self) -> float:
        """Share of listed files that earned credit. Vacuously 1.0 when nothing
        was listed — harmless, since an empty answer has recall 0 and so F1 0."""
        if self.n_listed == 0:
            return 1.0
        return self.n_matched / self.n_listed

    @property
    def f1(self) -> float:
        """Harmonic mean of precision and recall; 0.0 when both are 0.

        Because precision and recall share one 1-to-1 true-positive count, at
        the 0.5 pass bar this subsumes the "cap |answer| at 3x |GT|" alternative
        for EVERY matcher, not just exact equality: full recall passes iff
        |answer| <= 3|GT|. See docs/file_discovery_scoring_decision.md.
        """
        p, r = self.precision, self.recall
        if p + r == 0:
            return 0.0
        return 2 * p * r / (p + r)

    @property
    def passed(self) -> bool:
        return self.f1 >= FILE_SET_PASS_THRESHOLD

    def detail(self) -> str:
        """Human-readable verdict."""
        return (
            f"{self.n_matched}/{self.n_required} required files found, "
            f"{self.n_listed} listed; "
            f"P={self.precision:.2f} R={self.recall:.2f} F1={self.f1:.2f}"
        )


def _max_pairing(
    listed: Sequence[str],
    required: Sequence[str],
    match: Callable[[str, str], bool],
) -> int:
    """Size of a maximum 1-to-1 pairing between listed and required files.

    The true-positive count, and the reason a lenient matcher cannot buy credit:
    a listed file is worth at most one required file no matter how many it
    matches, and a required file is found at most once no matter how many
    spellings of it were listed. Kuhn's augmenting-path algorithm — required
    sets are a handful of files, so the quadratic is free.
    """
    credited_by = [-1] * len(required)  # required index -> listed index, or -1

    def assign(li: int, seen: List[bool]) -> bool:
        for ri, gt in enumerate(required):
            if seen[ri] or not match(listed[li], gt):
                continue
            seen[ri] = True
            # Take it if free, else make its current holder step aside.
            if credited_by[ri] == -1 or assign(credited_by[ri], seen):
                credited_by[ri] = li
                return True
        return False

    return sum(assign(li, [False] * len(required)) for li in range(len(listed)))


def score_file_set(
    agent_files: Sequence[str],
    required_files: Sequence[str],
    match: Callable[[str, str], bool] = operator.eq,
) -> FileSetScore:
    """Score a file-discovery answer against required files with set-F1.

    ``match(agent_path, required_path)`` decides whether one listed file names
    one required file; default is exact string equality. The matcher owns all
    path semantics — normalization, suffix policy, component boundaries — and
    this function owns the scoring math. Credit is awarded through a 1-to-1
    pairing (:func:`_max_pairing`), so a matcher can decide WHICH files are
    credited but never HOW MANY: no matcher, however lenient, can make
    over-listing free. That independence is the whole point of one primitive.

    Both lists are deduplicated by exact string first: repeating the right
    answer is not spray, and repeating filler earns no extra penalty. Near-
    duplicate spellings survive dedup, but the pairing already denies them a
    second credit, so they only cost precision — as filler should.

    Raises ValueError on empty ``required_files``: a checkpoint with nothing to
    find is a bug in the task, not a free pass for the agent.
    """
    required = list(dict.fromkeys(required_files))
    listed = list(dict.fromkeys(agent_files))
    return FileSetScore(
        n_required=len(required),
        n_listed=len(listed),
        n_matched=_max_pairing(listed, required, match),
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
