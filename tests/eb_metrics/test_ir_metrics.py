"""Tests for the ported CSB retrieval-quality metrics (eb_metrics.ir_metrics).

These lock the port's fidelity to CodeScaleBench ``ir_metrics.py`` — in
particular the empty-set asymmetry, the ``precision@k`` fixed-``k``
denominator, and the multi-repo path normalization that lets
``repo::path`` ground truth match ``/workspace/<repo>/...`` retrieved paths.
"""

from __future__ import annotations

import math

import pytest

from eb_metrics.ir_metrics import (
    compute_ir_scores,
    context_efficiency,
    f1_at_k,
    file_level_recall,
    mean_average_precision,
    mrr,
    ndcg_at_k,
    normalize_path,
    precision_at_k,
    recall_at_k,
)
from eb_metrics.retrieval_extraction import _align_retrieved_to_required


# ---------------------------------------------------------------------------
# normalize_path — multi-repo path canonicalization
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "raw,expected",
    [
        # repo::path and /workspace/<repo>/<path> name the same file and must
        # canonicalize alike — both keep the repo.
        (
            "kubernetes::pkg/kubelet/prober/worker.go",
            "kubernetes/pkg/kubelet/prober/worker.go",
        ),
        # The /workspace/ prefix goes; the repo dir STAYS — it identifies which
        # repo of a multi-repo task the file came from.
        (
            "/workspace/kubernetes/pkg/kubelet/prober/worker.go",
            "kubernetes/pkg/kubelet/prober/worker.go",
        ),
        ("workspace/grafana/public/app/x.ts", "grafana/public/app/x.ts"),
        ("chromium--2d05e315/third_party/blink/x.cc", "third_party/blink/x.cc"),
        ("a/pkg/server/main.go", "pkg/server/main.go"),
        ("b/lib/util.py", "lib/util.py"),
        ("  /Src/Main.PY  ", "src/main.py"),  # strip + lowercase
    ],
)
def test_normalize_path_canonicalizes(raw: str, expected: str) -> None:
    assert normalize_path(raw) == expected


def test_normalize_keeps_repo_lead() -> None:
    # The repo segment is never guessed away. "kubernetes" is what says WHICH
    # repo of a multi-repo task the file came from; dropping it is what let a
    # read of prometheus/pkg/x.go score a hit on grafana/pkg/x.go.
    assert normalize_path("kubernetes/pkg/kubelet/x.go") == "kubernetes/pkg/kubelet/x.go"
    assert normalize_path("src/server/handler.go") == "src/server/handler.go"


def test_normalize_does_not_conflate_root_and_nested_manifest() -> None:
    # The false equality that made a repo-qualified path a non-distinct key:
    # a workspace-root manifest and a crate manifest are DIFFERENT files.
    assert normalize_path("tokio/Cargo.toml") != normalize_path("tokio/tokio/Cargo.toml")


def test_normalize_does_not_conflate_same_path_in_different_repos() -> None:
    assert normalize_path("grafana/pkg/svc/x.go") != normalize_path("prometheus/pkg/svc/x.go")


# The two path conventions are reconciled by _align_retrieved_to_required, not
# by normalize_path guessing at a repo lead. These assert that at the pipeline
# level — the level that actually scores a run. (Asserting on normalize_path in
# isolation is what previously produced a confident wrong conclusion about
# whether a repo-qualified key survives to the join: it does not, on its own.)


def test_repo_relative_gt_matches_workspace_read_through_align() -> None:
    required = ["kubernetes/pkg/kubelet/prober/worker.go"]
    retrieved = ["/workspace/kubernetes/pkg/kubelet/prober/worker.go"]
    scores = compute_ir_scores(
        _align_retrieved_to_required(retrieved, required), required, "t", "baseline"
    )
    assert scores.file_recall == 1.0


def test_non_code_dir_lead_now_matches_through_align() -> None:
    # Previously pinned as a known CSB limitation: grafana's repo-relative paths
    # lead with `public/`, which the old lead-strip ate on the GT side, leaving
    # the two sides one level apart and silently undercounting recall. Carrying
    # the repo on both sides and suffix-aligning makes it a hit.
    required = ["grafana/public/app/plugins/datasource/jaeger/x.ts"]
    retrieved = ["/workspace/grafana/public/app/plugins/datasource/jaeger/x.ts"]
    scores = compute_ir_scores(
        _align_retrieved_to_required(retrieved, required), required, "t", "baseline"
    )
    assert scores.file_recall == 1.0


# ---------------------------------------------------------------------------
# Core @k metrics — formulas and denominators
# ---------------------------------------------------------------------------


def test_precision_at_k_divides_by_k_not_len() -> None:
    # 3 retrieved, all relevant, k=10 → 3/10, NOT 3/3.
    retrieved = ["a.py", "b.py", "c.py"]
    relevant = {"a.py", "b.py", "c.py"}
    assert precision_at_k(retrieved, relevant, 10) == 0.3
    assert precision_at_k(retrieved, relevant, 3) == 1.0


def test_recall_at_k_is_found_over_relevant() -> None:
    retrieved = ["a.py", "x.py"]
    relevant = {"a.py", "b.py"}
    assert recall_at_k(retrieved, relevant, 5) == 0.5


def test_f1_at_k_harmonic_mean() -> None:
    retrieved = ["a.py", "x.py"]
    relevant = {"a.py", "b.py"}
    p = precision_at_k(retrieved, relevant, 2)  # 1/2
    r = recall_at_k(retrieved, relevant, 2)  # 1/2
    assert f1_at_k(retrieved, relevant, 2) == pytest.approx(2 * p * r / (p + r))


def test_mrr_reciprocal_rank_of_first_hit() -> None:
    assert mrr(["x.py", "y.py", "a.go"], {"a.go"}) == pytest.approx(1 / 3)
    assert mrr(["a.go"], {"a.go"}) == 1.0
    assert mrr(["x.py"], {"a.go"}) == 0.0


def test_ndcg_perfect_and_partial() -> None:
    relevant = {"a.py", "b.py"}
    # Both relevant items first → perfect nDCG.
    assert ndcg_at_k(["a.py", "b.py"], relevant, 2) == pytest.approx(1.0)
    # One relevant at rank 2 only.
    dcg = 1.0 / math.log2(3)
    idcg = 1.0 / math.log2(2) + 1.0 / math.log2(3)
    assert ndcg_at_k(["x.py", "a.py"], relevant, 2) == pytest.approx(dcg / idcg)


def test_map_single_query_average_precision() -> None:
    # hits at ranks 1 and 3 of 2 relevant → (1/1 + 2/3) / 2.
    retrieved = ["a.py", "x.py", "b.py"]
    relevant = {"a.py", "b.py"}
    assert mean_average_precision(retrieved, relevant) == pytest.approx((1.0 + 2 / 3) / 2)


# ---------------------------------------------------------------------------
# Empty-set asymmetry — must be preserved verbatim from CSB
# ---------------------------------------------------------------------------


def test_empty_relevant_set_asymmetry() -> None:
    retrieved = ["a.py"]
    empty: set[str] = set()
    # recall-family → 1.0 (nothing to miss); precision/nDCG → 0.0.
    assert recall_at_k(retrieved, empty, 5) == 1.0
    assert file_level_recall(retrieved, empty) == 1.0
    assert mean_average_precision(retrieved, empty) == 1.0
    assert precision_at_k(retrieved, empty, 5) == 0.0
    assert ndcg_at_k(retrieved, empty, 5) == 0.0


def test_empty_retrieved_set() -> None:
    relevant = {"a.py"}
    assert recall_at_k([], relevant, 5) == 0.0
    assert precision_at_k([], relevant, 5) == 0.0
    assert file_level_recall([], relevant) == 0.0
    assert context_efficiency([], relevant) == 0.0
    assert mrr([], relevant) == 0.0


def test_zero_intersection() -> None:
    assert file_level_recall(["x.py"], {"a.py"}) == 0.0
    assert context_efficiency(["x.py"], {"a.py"}) == 0.0
    assert f1_at_k(["x.py"], {"a.py"}, 5) == 0.0


# ---------------------------------------------------------------------------
# compute_ir_scores — end-to-end, realistic multi-repo
# ---------------------------------------------------------------------------


def test_compute_ir_scores_multi_repo_match() -> None:
    gt = [
        "kubernetes::pkg/kubelet/prober/worker.go",
        "kubernetes::pkg/kubelet/prober/worker_test.go",
    ]
    retrieved = [
        "/workspace/kubernetes/pkg/kubelet/prober/worker.go",
        "/workspace/kubernetes/README.md",
    ]
    s = compute_ir_scores(retrieved, gt, "err-prov-04", "baseline")

    assert s.n_ground_truth == 2
    assert s.n_retrieved == 2
    assert s.n_overlap == 1
    assert s.file_recall == 0.5
    assert s.context_efficiency == 0.5
    assert s.recall[10] == 0.5
    assert s.precision[10] == 0.1  # 1 hit / k=10
    # Everything rounded to 4 dp and dict-keyed by the default k set.
    assert set(s.precision) == {1, 3, 5, 10}


def test_compute_ir_scores_dedup_ground_truth_by_normalized_form() -> None:
    # The two spellings of one file — repo::path and /workspace/<repo>/path —
    # collapse to a single ground-truth entry.
    gt = ["kubernetes::pkg/a.go", "/workspace/kubernetes/pkg/a.go"]
    s = compute_ir_scores(["kubernetes/pkg/a.go"], gt, "t", "c")
    assert s.n_ground_truth == 1
    assert s.file_recall == 1.0


def test_compute_ir_scores_to_dict_roundtrip() -> None:
    s = compute_ir_scores(["a.py"], ["a.py"], "t", "c")
    d = s.to_dict()
    assert d["file_recall"] == 1.0
    assert d["n_overlap"] == 1
    assert set(d["recall"]) == {1, 3, 5, 10}
