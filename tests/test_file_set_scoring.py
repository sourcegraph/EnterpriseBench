"""
Set-F1 scoring for file-discovery checkpoints (bead EnterpriseBench-vdeyx).

Pins the over-listing fix: spray answers now pay in precision. Rationale and
options considered: docs/file_discovery_scoring_decision.md.
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
    assert FILE_SET_PASS_THRESHOLD == 0.5
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
    """Both lists count distinct entries only; repeats neither help nor hurt."""
    result = score_file_set(["a/b.py"] * 5, ["a/b.py"])
    assert result.n_listed == 1
    assert result.f1 == 1.0


def test_required_list_is_deduplicated():
    result = score_file_set(["a/b.py"], ["a/b.py", "a/b.py"])
    assert result.n_required == 1
    assert result.f1 == 1.0


# ------------------------------------------------------------------- edge cases


def test_empty_required_files_is_rejected_not_scored():
    """An empty answer against empty GT must not score a vacuous 1.0."""
    with pytest.raises(ValueError, match="no required files"):
        score_file_set([], [])
    with pytest.raises(ValueError, match="no required files"):
        score_file_set(["a/b.py"], [])


# ------------------------------------------------------------- matcher-agnostic


def test_default_matcher_is_exact_string_equality():
    result = score_file_set(["httpx/_config.py"], ["httpx/httpx/_config.py"])
    assert result.f1 == 0.0


def test_custom_matcher_composes():
    """The suffix policy (or ssikq's component-suffix, when it lands) plugs in
    without touching the scoring math."""
    result = score_file_set(
        ["httpx/httpx/_config.py"], ["httpx/_config.py"], match=suffix_match
    )
    assert result.f1 == 1.0


# ------------------------------------------ a lenient matcher cannot buy credit
#
# The load-bearing tests: without the 1-to-1 pairing, each exploit below scores
# a passing 1.0 under the very matcher the shipped checks use (`--policy
# suffix`).


def test_padding_variants_of_one_found_file_cannot_inflate_precision():
    """Spray, laundered through the suffix matcher: 50 differently-prefixed
    spellings of the one required file all suffix-match it. Counting each as
    'relevant' would pin precision at 1.0 and re-open the exploit; the pairing
    credits the required file exactly once, so the other 50 are pure filler."""
    required = ["httpx/httpx/_config.py"]
    listed = required + [f"cand{i}/httpx/httpx/_config.py" for i in range(50)]

    result = score_file_set(listed, required, match=suffix_match)

    assert result.n_matched == 1  # found once, not 51 times
    assert result.n_listed == 51
    assert result.recall == 1.0
    assert result.precision == pytest.approx(1 / 51)
    assert not result.passed


def test_one_vague_guess_cannot_claim_several_required_files():
    """The ambiguity half: under a basename matcher, `_client.py` names a file
    in every repo. One guess is worth one file, so recall stays 0.5."""

    def basename_match(agent_path: str, required_path: str) -> bool:
        return agent_path.rsplit("/", 1)[-1] == required_path.rsplit("/", 1)[-1]

    result = score_file_set(
        ["anywhere/_client.py"],
        ["httpx/_client.py", "httpcore/_client.py"],
        match=basename_match,
    )

    assert result.n_matched == 1  # not 2
    assert result.recall == 0.5
    assert result.precision == 1.0
    assert result.f1 == pytest.approx(2 / 3)


@pytest.mark.parametrize(
    "required",
    [
        ["_config.py", "httpx/httpx/_config.py"],
        ["httpx/httpx/_config.py", "_config.py"],
    ],
)
def test_pairing_is_maximum_so_ground_truth_order_cannot_move_the_score(required):
    """The pairing is maximum, not greedy-first: a specific guess must not be
    wasted on a required file a vaguer one also covers. Here `other/_config.py`
    can only pay for `_config.py`, so greedy — which lets the first listed file
    take `_config.py` — finds 1 under one GT order and 2 under the other. A
    score that moves when a task author reorders a JSON array is not a score;
    the maximum pairing finds both either way."""
    result = score_file_set(
        ["httpx/httpx/_config.py", "other/_config.py"], required, match=suffix_match
    )
    assert result.n_matched == 2
    assert result.f1 == 1.0


# ------------------------------------------- count-based construction (ssikq)


def test_from_counts_matches_pairwise_math():
    """file_extraction.py's score_answer() produces (matched set, ambiguous
    list), not pairwise hits — it adopts by constructing from counts. Ambiguous
    guesses are listed but credit nothing, so they cost precision."""
    by_counts = FileSetScore(n_required=4, n_listed=9, n_matched=3)
    assert by_counts.recall == 0.75
    assert by_counts.precision == pytest.approx(3 / 9)
    assert by_counts.f1 == pytest.approx(6 / 13)


def test_incoherent_counts_are_rejected():
    # More required files credited than exist.
    with pytest.raises(ValueError, match="outside"):
        FileSetScore(n_required=2, n_listed=3, n_matched=3)
    # Credit without listing anything — the shape that fabricated a passing 1.0
    # out of an empty answer before the counts shared one true-positive total.
    with pytest.raises(ValueError, match="outside"):
        FileSetScore(n_required=5, n_listed=0, n_matched=5)
    with pytest.raises(ValueError, match="no required files"):
        FileSetScore(n_required=0, n_listed=5, n_matched=0)
    with pytest.raises(ValueError, match="outside"):
        FileSetScore(n_required=1, n_listed=-1, n_matched=0)
    with pytest.raises(ValueError, match="outside"):
        FileSetScore(n_required=1, n_listed=1, n_matched=-1)


# ----------------------------------------------------------------------- detail


def test_detail_exposes_found_total_listed_and_prf():
    result = score_file_set(["a/b.py", "x/y.py", "z/w.py"], ["a/b.py", "c/d.py"])
    detail = result.detail()
    assert "1/2" in detail
    assert "3 listed" in detail
    assert "P=0.33" in detail
    assert "R=0.50" in detail
    assert "F1=0.40" in detail
