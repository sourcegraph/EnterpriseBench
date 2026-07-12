"""Information-retrieval metrics for EnterpriseBench.

A faithful port of the retrieval-quality core of CodeScaleBench's
``scripts/csb_metrics/ir_metrics.py`` (canonical source on CSB ``main``).
Provides standard IR metrics (precision, recall, F1, MRR, nDCG, MAP) plus
file-level recall and context efficiency over *file-path* lists — the unit
of retrieval CSB scores against.

Scope (co-4cb.1 / jn73.1 — the retrieval-quality metric axis / *reward* side)
------------------------------------------------------------------------
The deterministic retrieval-quality computation (precision/recall/F1/MRR/
nDCG/MAP + file_recall + context_efficiency), the named localization@1/@k
hit-rate helpers, and — as of jn73.1 — the turns-/tokens-to-first-relevant
fields (``turns_to_first_relevant``, ``tokens_to_first_relevant``,
``ttfr_seconds``) live here. The CSB module's *dollar-cost* fields
(``cost_before_first_relevant``, ``agent_time_to_first_relevant`` priced in
USD) remain **out of scope**: they belong to the separate cost lever, not
the stratum-labeling axis (jn73.2 Gate C keys on turns-to-first-relevant,
not dollar cost). :class:`IRScores`' TTFR fields are populated by
:func:`eb_metrics.retrieval_extraction.compute_run_ir_scores` (which has
trace-file access for timestamps/token usage) rather than by
:func:`compute_ir_scores` itself, which stays pure list-of-paths math.

ZFC compliance
--------------
Every function here is pure, deterministic set/count math over path
strings — no model call, no semantic classification, no learned
thresholds. Matching is exact string equality on normalized, repo-relative,
lowercased paths (see :func:`normalize_path`).
"""

from __future__ import annotations

import math
import re
from dataclasses import dataclass, field

__all__ = [
    "normalize_path",
    "precision_at_k",
    "recall_at_k",
    "f1_at_k",
    "mrr",
    "ndcg_at_k",
    "mean_average_precision",
    "file_level_recall",
    "context_efficiency",
    "localization_at_1",
    "localization_at_k",
    "IRScores",
    "compute_ir_scores",
    "DEFAULT_K_VALUES",
]

DEFAULT_K_VALUES: tuple[int, ...] = (1, 3, 5, 10)

# First segment of a multi-repo path that must NOT be stripped as a repo
# name, because it is a conventional code directory that legitimately leads
# a repo-relative path. Ported verbatim from CSB ``_normalize``.
_CODE_DIRS = frozenset(
    {
        "src", "lib", "pkg", "cmd", "internal", "api", "app", "third_party",
        "test", "tests", "docs", "build", "tools", "scripts", "vendor",
        "include", "client", "server", "core", "util", "common",
    }
)

# ``name--<6-12 hex>/`` repo-slug prefix used by the sandbox workspace layout.
_REPO_SLUG_RE = re.compile(r"^[a-zA-Z0-9_.-]+--[a-f0-9]{6,12}/")


def normalize_path(path: str) -> str:
    """Normalize a file path for comparison.

    Strips ``repo::path`` prefixes (multi-repo ground-truth format),
    ``/workspace/`` container prefixes, a ``name--<hexhash>/`` repo-slug
    subdirectory, a single leading plain repo-name segment (guarded by the
    :data:`_CODE_DIRS` allowlist so real repo-relative leads survive),
    ``a/``/``b/`` diff prefixes, and leading/trailing slashes, then
    lowercases the result. Faithful port of CSB ``ir_metrics._normalize``.
    """
    p = path.strip()
    # repo::path prefix (multi-repo ground truth from oracle_answer.json).
    if "::" in p:
        p = p.split("::", 1)[1]
    # /workspace/ container prefix (sandbox paths).
    for prefix in ("/workspace/", "workspace/"):
        if p.startswith(prefix):
            p = p[len(prefix):]
    # Repo-slug prefix: "chromium--2d05e315/third_party/..." → "third_party/...".
    if _REPO_SLUG_RE.match(p):
        p = p.split("/", 1)[1] if "/" in p else p
    # Plain repo-name prefix (e.g. "kubernetes/pkg/..." when GT is "pkg/..."):
    # only strip when the first segment is not a file (no dot), the path has
    # further depth, and the segment is not a conventional code directory.
    parts = p.split("/", 1)
    if len(parts) == 2 and "." not in parts[0] and "/" in parts[1]:
        if parts[0].lower() not in _CODE_DIRS:
            p = parts[1]
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


def localization_at_1(retrieved: list[str], relevant: set[str]) -> float:
    """Localization accuracy@1: 1.0 iff the very first retrieved file is relevant.

    Thin wrapper over :func:`precision_at_k` — with a single-item top-k,
    precision@1 (``hits / 1``) already *is* the binary "is rank 1 correct"
    indicator, so no new arithmetic is introduced. Distinct from
    ``recall_at_k(..., 1)``, which divides by ``len(relevant)`` instead of
    by 1 and so is not a 0/1 accuracy signal when there is more than one
    ground-truth file.
    """
    return precision_at_k(retrieved, relevant, 1)


def localization_at_k(retrieved: list[str], relevant: set[str], k: int) -> float:
    """Localization accuracy@k: 1.0 iff ANY relevant file ranks within the
    first ``k`` retrieved (a.k.a. hit-rate@k / success@k), 0.0 otherwise.

    Thin wrapper over :func:`mrr`: ``mrr() == 1 / rank_of_first_relevant``,
    so the first relevant file ranks within ``k`` exactly when
    ``mrr() >= 1 / k``. Unlike ``recall_at_k`` — the *fraction* of the
    ground-truth set found within the top ``k`` — this is a single 0/1 hit
    indicator per trial: "did the agent open a correct file within its
    first k reads." That is the localization-breadth signal downstream
    stratum labeling (jn73.2 Gate C) keys on. ``k <= 0`` and an empty
    ``relevant`` set (no rank exists to hit) both yield 0.0, matching
    ``mrr``'s own behavior on an empty relevant set.

    Reported in every ``IRScores`` payload; no gate consumes it yet. (Gate C
    keys on turns-to-first-relevant, not on this — see
    ``scripts/validation/stratum_selector.score_gate_c``.)
    """
    if k <= 0:
        return 0.0
    return 1.0 if mrr(retrieved, relevant) >= (1.0 / k) else 0.0


@dataclass(frozen=True)
class IRScores:
    """Retrieval-quality scores for a single (task, config) pair.

    ``precision``/``recall``/``f1``/``ndcg``/``localization`` are keyed by
    ``k``; the scalar fields (``file_recall``, ``context_efficiency``, …)
    have no cutoff. All values are rounded to 4 decimals by
    :func:`compute_ir_scores`.

    The TTFR fields (``turns_to_first_relevant``, ``tokens_to_first_relevant``,
    ``ttfr_seconds``, ``ttfr_coverage``) are **not** populated by
    :func:`compute_ir_scores` — it is pure list-of-paths math with no trace
    access. They default to ``None``/``"not_computed"`` here and are filled
    in by :func:`eb_metrics.retrieval_extraction.compute_run_ir_scores` via
    :func:`dataclasses.replace` once it has joined the trace file. A caller
    that sees ``ttfr_coverage == "not_computed"`` is looking at a bare
    :func:`compute_ir_scores` result, not a missing signal — never treat it
    as "the agent found nothing."
    """

    task_id: str
    config_name: str
    precision: dict[int, float] = field(default_factory=dict)
    recall: dict[int, float] = field(default_factory=dict)
    f1: dict[int, float] = field(default_factory=dict)
    ndcg: dict[int, float] = field(default_factory=dict)
    localization: dict[int, float] = field(default_factory=dict)
    mrr: float = 0.0
    map_score: float = 0.0
    file_recall: float = 0.0
    context_efficiency: float = 0.0
    n_retrieved: int = 0
    n_ground_truth: int = 0
    n_overlap: int = 0

    # Turns-/tokens-to-first-relevant (TTFR). See class docstring — filled
    # post-hoc by retrieval_extraction.compute_run_ir_scores, not here.
    turns_to_first_relevant: int | None = None
    tokens_to_first_relevant: int | None = None
    ttfr_seconds: float | None = None
    ttfr_coverage: str = "not_computed"

    def to_dict(self) -> dict:
        return {
            "task_id": self.task_id,
            "config_name": self.config_name,
            "precision": dict(self.precision),
            "recall": dict(self.recall),
            "f1": dict(self.f1),
            "ndcg": dict(self.ndcg),
            "localization": dict(self.localization),
            "mrr": self.mrr,
            "map_score": self.map_score,
            "file_recall": self.file_recall,
            "context_efficiency": self.context_efficiency,
            "n_retrieved": self.n_retrieved,
            "n_ground_truth": self.n_ground_truth,
            "n_overlap": self.n_overlap,
            "turns_to_first_relevant": self.turns_to_first_relevant,
            "tokens_to_first_relevant": self.tokens_to_first_relevant,
            "ttfr_seconds": self.ttfr_seconds,
            "ttfr_coverage": self.ttfr_coverage,
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
        localization={k: round(localization_at_k(retrieved, relevant, k), 4) for k in ks},
        mrr=round(mrr(retrieved, relevant), 4),
        map_score=round(mean_average_precision(retrieved, relevant), 4),
        file_recall=round(file_level_recall(retrieved, relevant), 4),
        context_efficiency=round(context_efficiency(retrieved, relevant), 4),
        n_retrieved=len(retrieved),
        n_ground_truth=len(relevant),
        n_overlap=len(overlap),
    )
