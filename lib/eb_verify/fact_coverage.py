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
operating threshold). Swapping in a real sentence-transformer embedder does
NOT close this gap either (see EnterpriseBench-8qya.1's calibration): both
embedder families hit the identical best-F1 operating point on real GT-
derived pairs and both fail the same order-reversed/negation cases.

``fact_polarity_check.apply_polarity_check`` (a sibling module) implements
the LLM-backed post-check mitigation: it re-verifies each recovered
``coverage()``/``precision()`` match's direction and polarity via an LLM
(``eb_verify.judge``'s backend), downgrading only confirmed disagreements.
It is opt-in and does not change this module's behavior or its "no network,
no persisted state" guarantee. The deployment boundary above is NOT lifted
by its existence alone -- that still requires validating it closes the gap
on a real adversarial hold-out (see EnterpriseBench-i02m) and, for any live
task wiring, provisioning eb_verify's Python deps into the scoring sandbox
(neither is done yet). Note ``plugins/fact_triples.py``'s own semantic
fallback matcher does not consume ``coverage()``/``FactMatch`` at all (it
matches via its own greedy assignment over a raw similarity matrix), so it
is unaffected by and not automatically improved by this post-check.

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

from dataclasses import dataclass
from typing import Protocol, Sequence

import numpy as np
from sklearn.feature_extraction.text import TfidfVectorizer

# Calibrated on the labeled pair set in fact_coverage_calibration.py:
# best F1 = 0.828 at threshold 0.40 for the TF-IDF char 3-5-gram default
# embedder (perfect recall of paraphrase pairs; 5/12 near-miss false
# positives, dominated by order-blind cases — see calibration module).
DEFAULT_THRESHOLD = 0.40


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
