"""
Semantic fact coverage as partial-credit retrieval scoring (prototype).

A ground-truth answer for a context-gathering task is decomposed into atomic
*facts* (short natural-language statements about the codebase). An agent's
answer is decomposed the same way. Coverage = recall of GT facts by the
candidate set under cosine similarity of statement embeddings; precision =
fraction of candidate facts grounded in GT (the ungrounded-assertion rate is
``1 - precision``). Adapted from the kge harness ``coverage()`` and the
``dup_of`` provenance pattern in its app.py.

The default embedder is a deterministic TF-IDF over character 3-5-grams
(sklearn), fit per call on the union of GT + candidate statements. No network,
no persisted state. A real embedding model can be plugged in later via the
``Embedder`` Protocol.

Deployment boundary (do not wire into adversarial scoring)
-----------------------------------------------------------
The TF-IDF default is suitable ONLY for cooperative partial-credit recall
(honest agent output, missed-fact detection). Independent verification on
held-out adversarial pairs showed it cannot be trusted where an agent could
game the score: bag-of-ngrams is word-order-blind, so a direction-reversed
relation ("scheduler restarts the worker" vs "worker restarts the scheduler")
scores similarity 1.000; a negated statement scores ~0.956; and a genuine
paraphrase with divergent surface forms can miss at ~0.30 (below the 0.40
operating threshold). Before this module scores anything agents are graded
on, plug a real embedding model in via ``Embedder`` and re-run the
calibration sweep in ``fact_coverage_calibration`` (ideally regrown from
actual task GT facts); a directional/negation post-check on matched pairs is
the cheap alternative mitigation.

Mapping onto lib/eb_verify/scoring.py checkpoints
-------------------------------------------------
``scoring.compute_score`` takes ``CheckpointResult(name, weight, passed,
score)`` items and returns the weight-normalized sum of ``score * weight``.
Fact coverage slots in as follows:

* A *checkpoint* is a cluster of GT facts (e.g. all facts tagged
  ``root-cause``, or all facts about one repo in a multi-repo chain). Tags on
  ``Fact`` carry the cluster key.
* Checkpoint score = ``coverage(cluster_facts, candidate_facts).recall`` —
  the fraction of that cluster's facts recovered. This is exactly the
  0.0-1.0 partial-credit ``score`` field of ``CheckpointResult``.
* ``passed`` = ``recall >= pass_bar`` for the cluster (task-configurable).
* Per-fact provenance (``FactMatch``) goes into ``CheckpointResult.detail``
  so a failed checkpoint names exactly which GT facts were missed and which
  candidate statement came closest.
* An optional extra checkpoint can penalize hallucination:
  ``score = precision(candidate_facts, all_gt_facts).recall``.

Example::

    for cluster_name, cluster in clusters.items():
        cov = coverage(cluster, candidate_facts, threshold=DEFAULT_THRESHOLD)
        results.append(CheckpointResult(
            name=f"facts:{cluster_name}",
            weight=weights[cluster_name],
            passed=cov.recall >= 0.99,
            score=cov.recall,
            detail=cov.provenance_summary(),
        ))
    total = compute_score(results)
"""

from __future__ import annotations

import importlib.util
from dataclasses import dataclass
from typing import TYPE_CHECKING, Protocol, Sequence

if TYPE_CHECKING:  # numpy/sklearn are imported lazily — see below.
    import numpy as np

# numpy and sklearn are imported inside the functions that use them, not at module
# scope. `eb_verify/__init__` re-exports the runner, which reaches this module, so a
# module-scope import made *every* importer of eb_verify (including the chain runner,
# which never scores facts) pay ~0.7s and ~100MB of TF-IDF machinery at startup.
# Because of that deferral, importing this module no longer proves the stack is
# usable, so callers who need to know must ask: scoring_deps_available (cheap, does
# not execute) or load_scoring_deps (definitive, does).

# Calibrated on the labeled pair set in fact_coverage_calibration.py:
# best F1 = 0.828 at threshold 0.40 for the TF-IDF char 3-5-gram default
# embedder (perfect recall of paraphrase pairs; 5/12 near-miss false
# positives, dominated by order-blind cases — see calibration module).
DEFAULT_THRESHOLD = 0.40


class ScoringDepsUnavailable(RuntimeError):
    """The deferred scoring stack is missing, or installed but unusable."""


def scoring_deps_available() -> bool:
    """Whether the scoring stack looks importable, without importing it.

    `plugins/__init__` keys the fact_triples registration off this, so a sandbox that
    ships no numpy drops the validator up front rather than discovering it mid-score.
    The check must not execute what it is checking for: it sits on the chain runner's
    import path, and paying ~0.8s there is the cost the deferral exists to avoid. Only
    top-level names are probed, since find_spec on a dotted path imports the parents.

    scipy is included because sklearn's TfidfVectorizer pulls it in; it is never named
    in an import here, but an install without it fails just as hard.

    Resolving without executing is also the limit of this check: a module that is
    present but broken looks fine here. load_scoring_deps is what settles that.
    """
    try:
        return all(
            importlib.util.find_spec(m) is not None
            for m in ("numpy", "scipy", "sklearn")
        )
    except (ImportError, ValueError):
        # A blocked or half-installed module can raise here rather than return None.
        return False


def load_scoring_deps() -> None:
    """Import the scoring stack, or raise ScoringDepsUnavailable.

    A broken dependency is under no obligation to raise ImportError: an ABI mismatch
    raises RuntimeError, a truncated shared object raises OSError, a bad C extension
    raises SystemError. Enumerating those types is a losing game, so this catches any
    Exception the import throws and re-raises the one type callers can guard on.

    Exception, not BaseException: a KeyboardInterrupt or SystemExit arriving during the
    import is process control, not a broken dependency, and must keep propagating.

    The broad catch is safe precisely because the try block holds nothing but the
    import. Scoring runs outside it, so a genuine bug in the scoring logic still
    propagates as itself instead of being laundered into "dependency missing".

    The real import cost lands here, inside a scoring call that needs the stack
    anyway. The chain runner never reaches it.
    """
    try:
        import numpy  # noqa: F401
        from sklearn.feature_extraction.text import TfidfVectorizer  # noqa: F401
    except Exception as exc:  # noqa: BLE001 — the try block is a single import
        raise ScoringDepsUnavailable(
            f"{type(exc).__name__}: {exc}" if str(exc) else type(exc).__name__
        ) from exc


@dataclass(frozen=True)
class Fact:
    """An atomic ground-truth or candidate fact.

    ``statement`` is the semantic payload; ``tags`` optionally carry
    checkpoint-cluster keys (see module docstring).
    """

    statement: str
    tags: tuple[str, ...] = ()


class Embedder(Protocol):
    """Embeds texts into L2-normalized row vectors, shape (n, d)."""

    def embed(self, texts: list[str]) -> np.ndarray: ...


class TfidfCharNgramEmbedder:
    """Deterministic local default: TF-IDF over char 3-5-grams, L2-normalized.

    Stateless — the vectorizer is fit on the texts of each ``embed`` call, so
    GT and candidate statements must be embedded in a single call to share a
    vector space (``coverage`` does this).
    """

    def __init__(self, ngram_range: tuple[int, int] = (3, 5)) -> None:
        self.ngram_range = ngram_range

    def embed(self, texts: list[str]) -> np.ndarray:
        import numpy as np
        from sklearn.feature_extraction.text import TfidfVectorizer

        if not texts:
            return np.zeros((0, 0))
        vectorizer = TfidfVectorizer(
            analyzer="char_wb",
            ngram_range=self.ngram_range,
            lowercase=True,
            norm="l2",
        )
        matrix = vectorizer.fit_transform(texts)
        return np.asarray(matrix.todense())


@dataclass(frozen=True)
class FactMatch:
    """Provenance for one GT fact (the dup_of pattern, reduced to best match).

    For ``precision()`` the direction is flipped: ``gt_index`` indexes the
    candidate list and ``best_match_index`` indexes the GT list.
    """

    gt_index: int
    best_match_index: int | None
    similarity: float
    recovered: bool


@dataclass(frozen=True)
class CoverageResult:
    """Recall of GT facts plus per-fact provenance."""

    recall: float
    threshold: float
    matches: tuple[FactMatch, ...] = ()

    def provenance_summary(self) -> str:
        lines = []
        for m in self.matches:
            status = "recovered" if m.recovered else "MISSED"
            best = "-" if m.best_match_index is None else str(m.best_match_index)
            lines.append(
                f"gt[{m.gt_index}] {status} best_match={best} sim={m.similarity:.3f}"
            )
        return "\n".join(lines)


def _validate_facts(facts: Sequence[Fact], label: str) -> None:
    for i, f in enumerate(facts):
        if not isinstance(f, Fact):
            raise ValueError(f"{label}[{i}] is not a Fact: {f!r}")
        if not f.statement or not f.statement.strip():
            raise ValueError(f"{label}[{i}] has an empty statement")


def _validate_threshold(threshold: float) -> None:
    if not 0.0 <= threshold <= 1.0:
        raise ValueError(f"threshold must be in [0, 1], got {threshold}")


def coverage(
    gt_facts: Sequence[Fact],
    candidate_facts: Sequence[Fact],
    embedder: Embedder | None = None,
    threshold: float = DEFAULT_THRESHOLD,
) -> CoverageResult:
    """Recall of GT facts by the candidate set.

    A GT fact is *recovered* if its max cosine similarity against the
    candidate statements is >= ``threshold``. Empty GT is vacuous success
    (recall 1.0); empty candidate with non-empty GT is total miss (0.0, with
    per-fact provenance recording no match).
    """
    _validate_threshold(threshold)
    _validate_facts(gt_facts, "gt_facts")
    _validate_facts(candidate_facts, "candidate_facts")

    if not gt_facts:
        return CoverageResult(recall=1.0, threshold=threshold, matches=())
    if not candidate_facts:
        miss_matches = tuple(
            FactMatch(gt_index=i, best_match_index=None, similarity=0.0, recovered=False)
            for i in range(len(gt_facts))
        )
        return CoverageResult(recall=0.0, threshold=threshold, matches=miss_matches)

    import numpy as np

    emb = (embedder or TfidfCharNgramEmbedder()).embed(
        [f.statement for f in gt_facts] + [f.statement for f in candidate_facts]
    )
    n_gt = len(gt_facts)
    gt_emb, cand_emb = emb[:n_gt], emb[n_gt:]

    sims = gt_emb @ cand_emb.T  # (n_gt, n_cand) cosine sims (rows normalized)
    matches: list[FactMatch] = []
    recovered_count = 0
    for i in range(n_gt):
        best_j = int(np.argmax(sims[i]))
        best_sim = float(sims[i, best_j])
        recovered = best_sim >= threshold
        recovered_count += recovered
        matches.append(
            FactMatch(
                gt_index=i,
                best_match_index=best_j,
                similarity=round(best_sim, 6),
                recovered=recovered,
            )
        )
    return CoverageResult(
        recall=recovered_count / n_gt, threshold=threshold, matches=tuple(matches)
    )


def precision(
    candidate_facts: Sequence[Fact],
    gt_facts: Sequence[Fact],
    embedder: Embedder | None = None,
    threshold: float = DEFAULT_THRESHOLD,
) -> CoverageResult:
    """Fraction of candidate facts grounded in GT (coverage in reverse).

    ``1 - result.recall`` is the ungrounded-assertion rate. In the returned
    ``matches``, ``gt_index`` indexes the candidate list and
    ``best_match_index`` indexes the GT list.
    """
    return coverage(candidate_facts, gt_facts, embedder=embedder, threshold=threshold)
