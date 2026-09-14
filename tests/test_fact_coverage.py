"""Tests for eb_verify.fact_coverage — semantic partial-credit retrieval scoring."""

from __future__ import annotations

import pytest

from eb_verify.fact_coverage import (
    CoverageResult,
    Fact,
    SentenceTransformerEmbedder,
    TfidfCharNgramEmbedder,
    coverage,
    precision,
)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

GT = [
    Fact(statement="httpx delegates connection pooling to httpcore"),
    Fact(statement="requests.Session persists cookies across requests via RequestsCookieJar"),
    Fact(statement="celery workers acknowledge tasks late when acks_late is enabled"),
]

CAND_EXACT = [Fact(statement=f.statement) for f in GT]


# ---------------------------------------------------------------------------
# Recall basics
# ---------------------------------------------------------------------------

def test_identical_statements_full_recall():
    result = coverage(GT, CAND_EXACT, threshold=0.9)
    assert result.recall == 1.0
    assert all(m.recovered for m in result.matches)
    # identical text → similarity ~1.0
    assert all(m.similarity == pytest.approx(1.0, abs=1e-6) for m in result.matches)


def test_empty_candidate_zero_recall():
    result = coverage(GT, [], threshold=0.5)
    assert result.recall == 0.0
    assert len(result.matches) == len(GT)
    for m in result.matches:
        assert m.recovered is False
        assert m.best_match_index is None
        assert m.similarity == 0.0


def test_empty_gt_full_recall():
    result = coverage([], CAND_EXACT, threshold=0.5)
    assert result.recall == 1.0
    assert result.matches == ()


def test_empty_gt_and_empty_candidate():
    result = coverage([], [], threshold=0.5)
    assert result.recall == 1.0
    assert result.matches == ()


# ---------------------------------------------------------------------------
# Provenance
# ---------------------------------------------------------------------------

def test_provenance_indices_point_to_best_candidate():
    # candidates shuffled + one distractor; each GT fact's best match is the
    # exact copy of itself at a known candidate index.
    cand = [
        Fact(statement="terraform stores resource state in terraform.tfstate"),  # distractor
        Fact(statement=GT[2].statement),  # matches gt_index 2
        Fact(statement=GT[0].statement),  # matches gt_index 0
        Fact(statement=GT[1].statement),  # matches gt_index 1
    ]
    result = coverage(GT, cand, threshold=0.9)
    assert result.recall == 1.0
    by_gt = {m.gt_index: m for m in result.matches}
    assert set(by_gt) == {0, 1, 2}
    assert by_gt[0].best_match_index == 2
    assert by_gt[1].best_match_index == 3
    assert by_gt[2].best_match_index == 1


def test_partial_recall_counts_only_recovered():
    cand = [Fact(statement=GT[0].statement)]  # only first GT fact present
    result = coverage(GT, cand, threshold=0.9)
    assert result.recall == pytest.approx(1 / 3)
    recovered = [m for m in result.matches if m.recovered]
    assert len(recovered) == 1
    assert recovered[0].gt_index == 0


# ---------------------------------------------------------------------------
# Determinism
# ---------------------------------------------------------------------------

def test_determinism_two_runs_identical():
    cand = [
        Fact(statement="connection pool management in httpx is handled by httpcore"),
        Fact(statement="a Session in requests keeps cookies between calls"),
    ]
    r1 = coverage(GT, cand, threshold=0.4)
    r2 = coverage(GT, cand, threshold=0.4)
    assert r1.recall == r2.recall
    assert [(m.gt_index, m.best_match_index, m.similarity, m.recovered) for m in r1.matches] == [
        (m.gt_index, m.best_match_index, m.similarity, m.recovered) for m in r2.matches
    ]


# ---------------------------------------------------------------------------
# Threshold monotonicity
# ---------------------------------------------------------------------------

def test_threshold_monotonicity_higher_never_increases_recall():
    cand = [
        Fact(statement="connection pool management in httpx is handled by the httpcore library"),
        Fact(statement="celery worker acks the task after execution when acks_late is on"),
        Fact(statement="grafana loads dashboards from provisioning configs"),
    ]
    thresholds = [i / 20 for i in range(1, 20)]
    recalls = [coverage(GT, cand, threshold=t).recall for t in thresholds]
    for lo, hi in zip(recalls, recalls[1:]):
        assert hi <= lo


# ---------------------------------------------------------------------------
# Precision (reverse direction)
# ---------------------------------------------------------------------------

def test_precision_flags_ungrounded_assertions():
    cand = [
        Fact(statement=GT[0].statement),  # grounded
        Fact(statement="kubernetes kubelet reports node status every 10 seconds"),  # ungrounded
    ]
    result = precision(cand, GT, threshold=0.9)
    assert result.recall == pytest.approx(0.5)  # here "recall" = grounded fraction
    by_idx = {m.gt_index: m for m in result.matches}  # gt_index = candidate index
    assert by_idx[0].recovered is True
    assert by_idx[1].recovered is False


def test_precision_empty_candidate_is_vacuously_one():
    result = precision([], GT, threshold=0.5)
    assert result.recall == 1.0


# ---------------------------------------------------------------------------
# Input validation
# ---------------------------------------------------------------------------

def test_empty_statement_rejected():
    with pytest.raises(ValueError, match="statement"):
        coverage([Fact(statement="   ")], CAND_EXACT, threshold=0.5)


def test_threshold_out_of_range_rejected():
    with pytest.raises(ValueError, match="threshold"):
        coverage(GT, CAND_EXACT, threshold=1.5)
    with pytest.raises(ValueError, match="threshold"):
        coverage(GT, CAND_EXACT, threshold=-0.1)


# ---------------------------------------------------------------------------
# Embedder contract
# ---------------------------------------------------------------------------

def test_default_embedder_rows_are_l2_normalized():
    import numpy as np

    emb = TfidfCharNgramEmbedder().embed([f.statement for f in GT])
    norms = np.linalg.norm(emb, axis=1)
    assert np.allclose(norms, 1.0)


def test_coverage_resolves_threshold_from_embedder_default():
    # No threshold arg: must resolve to the passed embedder's own
    # default_threshold, not the other embedder's calibration.
    result = coverage(GT, CAND_EXACT, embedder=TfidfCharNgramEmbedder())
    assert result.threshold == TfidfCharNgramEmbedder.default_threshold


def test_precision_resolves_threshold_from_embedder_default():
    result = precision(CAND_EXACT, GT, embedder=TfidfCharNgramEmbedder())
    assert result.threshold == TfidfCharNgramEmbedder.default_threshold


def test_coverage_result_type():
    result = coverage(GT, CAND_EXACT, threshold=0.9)
    assert isinstance(result, CoverageResult)
    assert result.threshold == 0.9
    assert isinstance(result.matches, tuple)


def test_empty_candidate_matches_are_a_tuple():
    result = coverage(GT, [], threshold=0.5)
    assert isinstance(result.matches, tuple)


# ---------------------------------------------------------------------------
# SentenceTransformerEmbedder — real embedding backend
# ---------------------------------------------------------------------------

def test_sentence_transformer_embedder_missing_dependency_raises_clear_error(monkeypatch):
    import sys

    monkeypatch.setitem(sys.modules, "sentence_transformers", None)
    with pytest.raises(ImportError, match=r"eb-verify\[embeddings\]"):
        SentenceTransformerEmbedder()


@pytest.fixture(scope="session")
def real_embedder() -> SentenceTransformerEmbedder:
    """Loads the pinned model once, shared by the tests below that take it as
    a parameter. Tests that build their embedder via EMBEDDER_FACTORIES
    (the calibration/adversarial parametrizations) load their own instance
    instead, to stay symmetric with the "tfidf" parametrization.
    """
    return SentenceTransformerEmbedder()


@pytest.mark.network
def test_sentence_transformer_embedder_rows_are_l2_normalized(real_embedder):
    import numpy as np

    emb = real_embedder.embed([f.statement for f in GT])
    norms = np.linalg.norm(emb, axis=1)
    assert np.allclose(norms, 1.0, atol=1e-5)


@pytest.mark.network
def test_sentence_transformer_embedder_determinism_two_runs_identical(real_embedder):
    texts = [f.statement for f in GT]
    a = real_embedder.embed(texts)
    b = real_embedder.embed(texts)
    assert (a == b).all()


@pytest.mark.network
def test_sentence_transformer_embedder_empty_texts(real_embedder):
    assert real_embedder.embed([]).shape == (0, 0)


# ---------------------------------------------------------------------------
# Calibration regression — locks in the experiment result
# ---------------------------------------------------------------------------

def test_sweep_rejects_embedder_and_sims_together():
    from eb_verify.fact_coverage_calibration import pair_similarities, sweep

    sims = pair_similarities(embedder=TfidfCharNgramEmbedder())
    with pytest.raises(ValueError, match="embedder.*sims"):
        sweep(embedder=TfidfCharNgramEmbedder(), sims=sims)


@pytest.mark.parametrize(
    "embedder_name",
    ["tfidf", pytest.param("sentence-transformer", marks=pytest.mark.network)],
)
def test_calibration_best_f1_and_threshold(embedder_name):
    from eb_verify.fact_coverage_calibration import EMBEDDER_FACTORIES, sweep

    embedder = EMBEDDER_FACTORIES[embedder_name]()
    _, best = sweep(embedder=embedder)
    assert best["f1"] >= 0.75, f"{embedder_name} fell below viability bar: {best}"
    assert best["threshold"] == embedder.default_threshold


@pytest.mark.parametrize(
    "embedder_name",
    ["tfidf", pytest.param("sentence-transformer", marks=pytest.mark.network)],
)
def test_adversarial_order_negation_pairs_are_not_solved_by_either_embedder(
    embedder_name,
):
    """Regression lock for the module docstring's deployment-boundary claim.

    Neither embedder correctly rejects direction-reversed or negated
    statement pairs at its own calibrated best-F1 threshold — swapping in a
    real embedding model does not, by itself, close the adversarial gap. If
    this test starts failing (i.e. an embedder starts getting these right),
    that is a real result and the module docstring's "Deployment boundary"
    section must be revisited, not just the assertion below.
    """
    from eb_verify.fact_coverage_calibration import (
        EMBEDDER_FACTORIES,
        pair_similarities,
        sweep,
    )

    embedder = EMBEDDER_FACTORIES[embedder_name]()
    sims = pair_similarities(embedder=embedder)
    _, best = sweep(sims=sims)
    threshold = best["threshold"]
    adversarial = [
        (p, s) for p, s in sims if p.kind in ("negation", "wrong-relation")
    ]
    correctly_rejected = sum(1 for p, s in adversarial if s < threshold)
    assert correctly_rejected == 0, (
        f"{embedder_name} now correctly rejects {correctly_rejected}/"
        f"{len(adversarial)} adversarial pairs — update the docstring instead "
        f"of this assertion if that's a genuine improvement"
    )
