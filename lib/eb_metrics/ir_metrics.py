"""Information-retrieval metrics for EnterpriseBench.

A faithful port of the retrieval-quality core of CodeScaleBench's
``scripts/csb_metrics/ir_metrics.py`` (canonical source on CSB ``main``).
Provides standard IR metrics (precision, recall, F1, MRR, nDCG, MAP) plus
file-level recall and context efficiency over *file-path* lists — the unit
of retrieval CSB scores against.

Scope (co-4cb.1 — the retrieval-quality metric axis / *reward* side)
-------------------------------------------------------------------
Only the deterministic retrieval-quality computation is ported here. The
CSB module's dollar-cost and time-to-context fields (``ttfr``,
``cost_before_first_relevant``, …) are intentionally **not** ported: they
belong to the *cost* lever (track A's separate child bead) and require
transcript timing/token extraction, which is out of scope for the
retrieval-quality axis.

ZFC compliance
--------------
Every function here is pure, deterministic set/count math over path
strings — no model call, no semantic classification, no learned
thresholds. Matching is exact string equality on normalized, workspace-relative,
lowercased paths (see :func:`normalize_path`).
"""

from __future__ import annotations

import math
import re
from dataclasses import dataclass, field

__all__ = [
    "is_workspace_qualified",
    "normalize_path",
    "precision_at_k",
    "recall_at_k",
    "f1_at_k",
    "mrr",
    "ndcg_at_k",
    "mean_average_precision",
    "file_level_recall",
    "context_efficiency",
    "IRScores",
    "compute_ir_scores",
    "DEFAULT_K_VALUES",
]

DEFAULT_K_VALUES: tuple[int, ...] = (1, 3, 5, 10)

# The sandbox container root every repo is cloned under, so the segment that
# follows one of these prefixes is the repo's clone directory.
_WORKSPACE_PREFIXES: tuple[str, ...] = ("/workspace/", "workspace/")

# ``name--<6-12 hex>/`` repo-slug prefix used by the sandbox workspace layout.
_REPO_SLUG_RE = re.compile(r"^[a-zA-Z0-9_.-]+--[a-f0-9]{6,12}/")


def is_workspace_qualified(path: str) -> bool:
    """True when the path is sandbox-absolute, so its leading segment *is* its repo.

    The complement — a path written from inside a clone, or scraped from a search
    payload — omits the repo, which then has to be inferred. Shared with
    :mod:`eb_metrics.retrieval_extraction` so that question cannot be answered
    one way here and another way there.
    """
    return path.strip().startswith(_WORKSPACE_PREFIXES)


def normalize_path(path: str) -> str:
    """Normalize a file path for comparison.

    Rewrites a ``repo::path`` ground-truth prefix to ``repo/path``; strips
    ``/workspace/`` container prefixes, a ``name--<hexhash>/`` repo-slug
    subdirectory, ``a/``/``b/`` diff prefixes, and leading/trailing slashes;
    lowercases the result.

    The leading repo segment is deliberately **kept**, so ``tokio/Cargo.toml``
    and ``tokio/tokio/Cargo.toml`` stay distinct keys and a read from the wrong
    repo of a multi-repo task cannot match ground truth. Reconciling a
    repo-relative path against a workspace-relative one is
    :func:`~eb_metrics.retrieval_extraction._align_retrieved_to_required`'s job;
    callers must align **before** scoring, as
    :func:`~eb_metrics.retrieval_extraction.compute_run_ir_scores` does.
    """
    p = path.strip()
    # repo::path names the same file as /workspace/<repo>/<path>, so the two
    # spellings must canonicalize alike: the separator becomes a slash.
    if "::" in p:
        p = p.replace("::", "/", 1)
    # /workspace/ container prefix (sandbox paths).
    for prefix in _WORKSPACE_PREFIXES:
        if p.startswith(prefix):
            p = p[len(prefix):]
    # Repo-slug prefix: "chromium--2d05e315/third_party/..." → "third_party/...".
    if _REPO_SLUG_RE.match(p):
        p = p.split("/", 1)[1] if "/" in p else p
    # Diff a/ b/ prefixes.
    if p.startswith(("a/", "b/")):
        p = p[2:]
    return p.strip("/").lower()


def precision_at_k(retrieved: list[str], relevant: set[str], k: int) -> float:
    """Precision@K = |relevant ∩ retrieved[:k]| / k.

    The denominator is ``k`` (not ``len(top_k)``), so under-retrieval is
    penalized — precision@10 with 3 all-relevant hits caps at 0.3.
    """
    if k <= 0:
        return 0.0
    top_k = retrieved[:k]
    if not top_k:
        return 0.0
    hits = sum(1 for f in top_k if normalize_path(f) in relevant)
    return hits / k


def recall_at_k(retrieved: list[str], relevant: set[str], k: int) -> float:
    """Recall@K = |relevant ∩ retrieved[:k]| / |relevant|.

    Empty relevant set → 1.0 (nothing to find); empty retrieved → 0.0.
    """
    if not relevant:
        return 1.0
    top_k = retrieved[:k]
    if not top_k:
        return 0.0
    norm_top = {normalize_path(f) for f in top_k}
    found = sum(1 for r in relevant if r in norm_top)
    return found / len(relevant)


def f1_at_k(retrieved: list[str], relevant: set[str], k: int) -> float:
    """F1@K = harmonic mean of precision@K and recall@K (0.0 when both 0)."""
    p = precision_at_k(retrieved, relevant, k)
    r = recall_at_k(retrieved, relevant, k)
    if p + r == 0:
        return 0.0
    return 2 * p * r / (p + r)


def mrr(retrieved: list[str], relevant: set[str]) -> float:
    """Mean Reciprocal Rank = 1 / rank_of_first_relevant (0.0 if none)."""
    for i, f in enumerate(retrieved):
        if normalize_path(f) in relevant:
            return 1.0 / (i + 1)
    return 0.0


def ndcg_at_k(retrieved: list[str], relevant: set[str], k: int) -> float:
    """Normalized Discounted Cumulative Gain @ K (binary relevance)."""
    if k <= 0 or not relevant:
        return 0.0
    top_k = retrieved[:k]
    dcg = 0.0
    for i, f in enumerate(top_k):
        if normalize_path(f) in relevant:
            dcg += 1.0 / math.log2(i + 2)  # i+2 because log2(1)=0
    ideal_k = min(k, len(relevant))
    idcg = sum(1.0 / math.log2(i + 2) for i in range(ideal_k))
    if idcg == 0:
        return 0.0
    return dcg / idcg


def mean_average_precision(retrieved: list[str], relevant: set[str]) -> float:
    """Average Precision for a single query (empty relevant → 1.0)."""
    if not relevant:
        return 1.0
    sum_prec = 0.0
    hits = 0
    for i, f in enumerate(retrieved):
        if normalize_path(f) in relevant:
            hits += 1
            sum_prec += hits / (i + 1)
    if hits == 0:
        return 0.0
    return sum_prec / len(relevant)


def file_level_recall(retrieved: list[str], relevant: set[str]) -> float:
    """Fraction of ground-truth files found anywhere in ``retrieved``.

    Recall with no ``k`` cutoff — the scalar the codeprobe ``low_recall``
    flag consumes. Empty relevant set → 1.0.
    """
    if not relevant:
        return 1.0
    norm_retrieved = {normalize_path(f) for f in retrieved}
    found = sum(1 for r in relevant if r in norm_retrieved)
    return found / len(relevant)


def context_efficiency(retrieved: list[str], relevant: set[str]) -> float:
    """Fraction of retrieved files that are relevant (precision over all).

    Empty retrieved → 0.0. ``relevant`` must already be normalized.
    """
    if not retrieved:
        return 0.0
    hits = sum(1 for f in retrieved if normalize_path(f) in relevant)
    return hits / len(retrieved)


@dataclass(frozen=True)
class IRScores:
    """Retrieval-quality scores for a single (task, config) pair.

    ``precision``/``recall``/``f1``/``ndcg`` are keyed by ``k``; the scalar
    fields (``file_recall``, ``context_efficiency``, …) have no cutoff.
    All values are rounded to 4 decimals by :func:`compute_ir_scores`.
    """

    task_id: str
    config_name: str
    precision: dict[int, float] = field(default_factory=dict)
    recall: dict[int, float] = field(default_factory=dict)
    f1: dict[int, float] = field(default_factory=dict)
    ndcg: dict[int, float] = field(default_factory=dict)
    mrr: float = 0.0
    map_score: float = 0.0
    file_recall: float = 0.0
    context_efficiency: float = 0.0
    n_retrieved: int = 0
    n_ground_truth: int = 0
    n_overlap: int = 0

    def to_dict(self) -> dict:
        return {
            "task_id": self.task_id,
            "config_name": self.config_name,
            "precision": dict(self.precision),
            "recall": dict(self.recall),
            "f1": dict(self.f1),
            "ndcg": dict(self.ndcg),
            "mrr": self.mrr,
            "map_score": self.map_score,
            "file_recall": self.file_recall,
            "context_efficiency": self.context_efficiency,
            "n_retrieved": self.n_retrieved,
            "n_ground_truth": self.n_ground_truth,
            "n_overlap": self.n_overlap,
        }


def compute_ir_scores(
    retrieved: list[str],
    ground_truth_files: list[str],
    task_id: str,
    config_name: str,
    k_values: tuple[int, ...] | list[int] | None = None,
) -> IRScores:
    """Compute all retrieval-quality metrics for one (task, config) pair.

    ``retrieved`` is the ordered, first-seen-unique list of file paths the
    agent accessed; ``ground_truth_files`` is the relevant set (repo::path
    strings or plain paths — both handled by :func:`normalize_path`). Order
    of ``retrieved`` matters for MRR/MAP/nDCG/@k, so callers must dedup and
    preserve first-seen order before calling.
    """
    ks = tuple(k_values) if k_values is not None else DEFAULT_K_VALUES
    relevant = {normalize_path(f) for f in ground_truth_files}
    norm_retrieved = {normalize_path(f) for f in retrieved}
    overlap = relevant & norm_retrieved

    return IRScores(
        task_id=task_id,
        config_name=config_name,
        precision={k: round(precision_at_k(retrieved, relevant, k), 4) for k in ks},
        recall={k: round(recall_at_k(retrieved, relevant, k), 4) for k in ks},
        f1={k: round(f1_at_k(retrieved, relevant, k), 4) for k in ks},
        ndcg={k: round(ndcg_at_k(retrieved, relevant, k), 4) for k in ks},
        mrr=round(mrr(retrieved, relevant), 4),
        map_score=round(mean_average_precision(retrieved, relevant), 4),
        file_recall=round(file_level_recall(retrieved, relevant), 4),
        context_efficiency=round(context_efficiency(retrieved, relevant), 4),
        n_retrieved=len(retrieved),
        n_ground_truth=len(relevant),
        n_overlap=len(overlap),
    )
