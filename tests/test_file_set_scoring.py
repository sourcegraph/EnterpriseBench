"""
Set-F1 scoring for file-discovery checkpoints (bead EnterpriseBench-vdeyx).

The exploit this closes: recall-only scoring (matched / len(required_files))
charges nothing for over-listing, so an agent that enumerates every plausible
file in the repo earns full credit on a 0.40-weight checkpoint without doing
the investigation the checkpoint measures.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent / "lib"))

from eb_verify.scoring import (  # noqa: E402
    FILE_SET_PASS_THRESHOLD,
    FileSetScore,
    score_file_set,
)


def suffix_match(agent_path: str, required_path: str) -> bool:
    """The historical blob policy: `gt in af or af.endswith(gt)`."""
    return required_path in agent_path or agent_path.endswith(required_path)


# ---------------------------------------------------------------- exploit repro


def test_spray_and_pray_no_longer_earns_full_credit():
    """The bead's repro: 1 required file, 30 listed. Recall-only scored 1.0."""
    required = ["httpx/httpx/_config.py"]
    spray = ["httpx/httpx/_config.py"] + [f"httpx/httpx/_mod{i}.py" for i in range(29)]

    result = score_file_set(spray, required)

    assert result.recall == 1.0  # the right file IS in there
    assert result.precision == pytest.approx(1 / 30)
    assert result.f1 == pytest.approx(2 / 31)
    assert not result.passed


def test_recall_only_blob_math_would_have_passed_the_spray():
    """Documents the delta: the replaced formula gave the spray full marks."""
    required = ["httpx/httpx/_config.py"]
    spray = ["httpx/httpx/_config.py"] + [f"httpx/httpx/_mod{i}.py" for i in range(29)]

    found = sum(1 for gt in required if any(suffix_match(af, gt) for af in spray))
    old_score = found / len(required)

    assert old_score == 1.0
    assert score_file_set(spray, required).f1 < FILE_SET_PASS_THRESHOLD


# ------------------------------------------------------------------ core scoring


def test_exact_answer_is_perfect():
    files = ["a/b.py", "a/c.py", "d/e.py", "f/g.py"]
    result = score_file_set(files, files)
    assert result.precision == 1.0
    assert result.recall == 1.0
    assert result.f1 == 1.0
    assert result.passed


def test_partial_recall_full_precision():
    required = ["a/b.py", "a/c.py", "d/e.py", "f/g.py"]
    result = score_file_set(["a/b.py", "d/e.py"], required)
    assert result.precision == 1.0
    assert result.recall == 0.5
    assert result.f1 == pytest.approx(2 / 3)
    assert result.passed


def test_nothing_matches_scores_zero():
    result = score_file_set(["x/y.py"], ["a/b.py"])
    assert result.precision == 0.0
    assert result.recall == 0.0
    assert result.f1 == 0.0  # harmonic mean guard: 0 when P + R == 0
    assert not result.passed


def test_empty_answer_scores_zero():
    result = score_file_set([], ["a/b.py"])
    assert result.recall == 0.0
    assert result.f1 == 0.0
    assert not result.passed


# ------------------------------------------------- the 3x-cap subsumption claim


def test_full_recall_at_exactly_3x_listing_sits_on_the_pass_threshold():
    """Full recall passes iff |answer| <= 3|GT| — option (b) as a theorem,
    not a constant: P=1/3, R=1 → F1=0.5 exactly."""
    required = ["a/b.py", "c/d.py"]
    listed = required + ["x/1.py", "x/2.py", "x/3.py", "x/4.py"]
    result = score_file_set(listed, required)
    assert result.f1 == pytest.approx(FILE_SET_PASS_THRESHOLD)
    assert result.passed  # threshold is inclusive


def test_full_recall_beyond_3x_listing_fails():
    required = ["a/b.py", "c/d.py"]
    listed = required + [f"x/{i}.py" for i in range(5)]  # 7 listed, 2 required
    result = score_file_set(listed, required)
    assert result.f1 == pytest.approx(4 / 9)
    assert not result.passed


# ------------------------------------------------------------------------ dedup


def test_agent_list_is_deduplicated():
    """Repeating the right answer is not spray; repeating filler is not extra
    penalty. Both lists count distinct entries only."""
    result = score_file_set(["a/b.py"] * 5, ["a/b.py"])
    assert result.n_listed == 1
    assert result.f1 == 1.0


def test_required_list_is_deduplicated():
    result = score_file_set(["a/b.py"], ["a/b.py", "a/b.py"])
    assert result.n_required == 1
    assert result.f1 == 1.0


# ------------------------------------------------------------------- edge cases


def test_both_empty_is_vacuously_perfect():
    result = score_file_set([], [])
    assert result.f1 == 1.0
    assert result.passed


def test_empty_required_with_listed_files_scores_zero():
    """Nothing to find, yet files were claimed: recall is vacuous (1.0) but
    precision is 0, so F1 is 0. GT should never be empty in practice —
    ground-truth loaders fail closed on it — but the math must not reward it."""
    result = score_file_set(["a/b.py"], [])
    assert result.recall == 1.0
    assert result.precision == 0.0
    assert result.f1 == 0.0
    assert not result.passed


# ------------------------------------------------------------- matcher-agnostic


def test_default_matcher_is_exact_string_equality():
    result = score_file_set(["httpx/_config.py"], ["httpx/httpx/_config.py"])
    assert result.f1 == 0.0


def test_custom_matcher_composes():
    """The suffix policy (or ssikq's component-suffix, when it lands) plugs in
    without touching the scoring math."""
    result = score_file_set(
        ["httpx/_config.py"], ["httpx/httpx/_config.py"], match=suffix_match
    )
    # 'httpx/httpx/_config.py'.endswith('httpx/_config.py') is False and the GT
    # is not a substring of the answer either — but the reverse holds:
    assert result.f1 == 0.0  # blob policy misses repo-prefixed GT (rmz1x bug 1)

    result = score_file_set(
        ["httpx/httpx/_config.py"], ["httpx/_config.py"], match=suffix_match
    )
    assert result.f1 == 1.0


# ------------------------------------------- count-based construction (ssikq)


def test_from_counts_matches_pairwise_math():
    """file_extraction.py's score_answer() produces (matched set, ambiguous
    list), not pairwise hits — it adopts by constructing from counts."""
    by_counts = FileSetScore(n_required=4, n_matched=3, n_listed=9, n_relevant=3)
    assert by_counts.recall == 0.75
    assert by_counts.precision == pytest.approx(3 / 9)
    assert by_counts.f1 == pytest.approx(2 * 0.75 * (3 / 9) / (0.75 + 3 / 9))


def test_incoherent_counts_are_rejected():
    with pytest.raises(ValueError):
        FileSetScore(n_required=2, n_matched=3, n_listed=3, n_relevant=3)
    with pytest.raises(ValueError):
        FileSetScore(n_required=2, n_matched=2, n_listed=1, n_relevant=2)
    with pytest.raises(ValueError):
        FileSetScore(n_required=-1, n_matched=0, n_listed=0, n_relevant=0)


# ----------------------------------------------------------------------- detail


def test_detail_exposes_found_total_listed_and_prf():
    result = score_file_set(["a/b.py", "x/y.py", "z/w.py"], ["a/b.py", "c/d.py"])
    detail = result.detail()
    assert "1/2" in detail
    assert "3 listed" in detail
    assert "P=0.33" in detail
    assert "R=0.50" in detail
    assert "F1=0.40" in detail


def test_pass_threshold_is_exported_and_inclusive():
    assert FILE_SET_PASS_THRESHOLD == 0.5
    on_threshold = FileSetScore(n_required=2, n_matched=2, n_listed=6, n_relevant=2)
    assert on_threshold.f1 == pytest.approx(0.5)
    assert on_threshold.passed
