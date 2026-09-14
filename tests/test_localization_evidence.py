"""localization_evidence must credit localization, not a filename dictionary.

The defect these pin (bead jn73.2.7, residual B): the enterprise-hard tasks
scored file localization with recall-only file_extraction, which matches bare
basenames and does not penalize over-listing. So a static basename dictionary
(``conn.rs``, ``stream.rs``, ``rtls.rs``), an over-listed answer (3 real paths +
200 junk), and a ``find /workspace`` dump all scored full credit with zero
investigation. The load-bearing cases here are those negatives — each must land
below the pass threshold — with positive controls so "score below pass" is not
satisfied by a scorer that fails everything.
"""

from __future__ import annotations

import pytest

from eb_verify.plugins.file_extraction import PASS_THRESHOLD
from eb_verify.plugins.localization_evidence import score_localization

# A three-file oracle spanning two repos, repo-qualified the way ground truth is.
GT = ["rustls/src/conn.rs", "rustls/src/stream.rs", "src/rtls.rs"]
# An oracle with a repo-root file, so a single-component path is the only
# truthful spelling for it (follow-redirects' index.js).
GT_ROOT = ["index.js", "follow-redirects/lib/foo.js", "axios/lib/adapters/http.js"]


def f1(gt: list[str], found: list[str]) -> float:
    return score_localization(gt, found)[0]


class TestPositiveControls:
    """An honest, precise answer still scores full — else the fix scores 0 for all."""

    def test_truthful_answer_scores_full(self) -> None:
        assert f1(GT, GT) == 1.0

    def test_directory_guessed_but_correct_scores_full(self) -> None:
        # `src/conn.rs` is not a bare basename and correctly suffix-matches.
        assert f1(GT, ["src/conn.rs", "src/stream.rs", "src/rtls.rs"]) == 1.0

    def test_partial_answer_scores_partial(self) -> None:
        score = f1(GT, ["rustls/src/conn.rs"])
        assert 0.0 < score < 1.0

    def test_root_level_oracle_credits_bare_name(self) -> None:
        # index.js is a repo-root file: its bare name is the truthful spelling.
        assert f1(GT_ROOT, GT_ROOT) == 1.0


class TestDictionaryAndDumpsScoreBelowPass:
    """The residual-B attacks: none may reach the pass threshold."""

    def test_bare_basename_dump_scores_below_pass(self) -> None:
        score = f1(GT, ["conn.rs", "stream.rs", "rtls.rs"])
        assert score < PASS_THRESHOLD
        assert score == 0.0  # bare basenames are not localizations at all

    def test_overlisted_answer_scores_below_pass(self) -> None:
        found = GT + [f"junk/dir/f{i}.py" for i in range(200)]
        assert f1(GT, found) < PASS_THRESHOLD

    def test_find_workspace_dump_scores_below_pass(self) -> None:
        # The maximal form: every real oracle path present, drowned in junk.
        found = GT + [f"pkg/mod/gen{i}.rs" for i in range(500)]
        assert f1(GT, found) < PASS_THRESHOLD

    def test_root_oracle_dictionary_attack_scores_below_pass(self) -> None:
        # Bare basenames of a mixed oracle: only the root file is credited, and
        # one of three at high precision is still below pass.
        score = f1(GT_ROOT, ["index.js", "foo.js", "http.js"])
        assert score < PASS_THRESHOLD


class TestPrecisionArithmetic:
    """Pin the precision numerator so a later change cannot loosen it silently."""

    def test_double_match_bare_and_qualified(self) -> None:
        # M3: a bare basename plus its dir-qualified twin — the bare one is not a
        # productive answer, so precision is 1/2 and the pair falls below pass.
        f, p, r, matched = score_localization(GT, ["conn.rs", "rustls/src/conn.rs"])
        assert p == 0.5
        assert r == pytest.approx(1 / 3)
        assert f < PASS_THRESHOLD

    def test_junk_dilutes_precision_not_just_recall(self) -> None:
        # Perfect recall, but padding drops precision and therefore F1.
        f, p, r, _ = score_localization(GT, GT + ["a/b/c.py"])
        assert r == 1.0
        assert p < 1.0
        assert f < 1.0

    def test_empty_answer_scores_zero(self) -> None:
        assert f1(GT, []) == 0.0
