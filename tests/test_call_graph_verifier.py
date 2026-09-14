"""Tests for the call_graph reachability verifier plugin.

Fixtures:
  1. All dead code correctly identified → high score
  2. Code with hidden callers claimed dead (in GT live list) → low precision
  3. Code reachable through dynamic dispatch → flagged, lower penalty
  4. Empty submission → near-zero score
  5. Partial identification → proportional score
  6. Perfect precision, partial recall → high score (precision-weighted)
  7. Plugin validate() integration via workspace files
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from eb_verify.plugins.call_graph import (
    CallGraphScore,
    CallGraphValidator,
    score_dead_code_claims,
)


# ── helpers ──────────────────────────────────────────────────────────────────

def _sym(file: str, symbol: str, **kwargs: str) -> dict:
    d = {"file": file, "symbol": symbol}
    d.update(kwargs)
    return d


GT_DEAD = [
    _sym("src/utils.py", "unused_helper"),
    _sym("src/utils.py", "old_formatter"),
    _sym("src/legacy.py", "deprecated_init"),
    _sym("src/legacy.py", "dead_callback"),
]

GT_LIVE = [
    _sym("src/core.py", "process_request", reachability="direct"),
    _sym("src/core.py", "handle_event", reachability="dynamic"),
    _sym("src/plugins.py", "load_plugin", reachability="reflection"),
    _sym("src/config.py", "get_setting", reachability="conditional"),
]


# ── Fixture 1: Actually dead code → high score ──────────────────────────────

class TestAllDeadCodeCorrect:
    def test_perfect_identification_scores_high(self) -> None:
        """Agent correctly identifies all dead code, claims nothing live."""
        claimed = list(GT_DEAD)
        result = score_dead_code_claims(claimed, GT_DEAD, GT_LIVE)

        assert result.true_positives == 4
        assert result.false_positives == 0
        assert result.false_negatives == 0
        assert result.precision == 1.0
        assert result.recall == 1.0
        assert result.total_score >= 0.95

    def test_perfect_score_is_one(self) -> None:
        result = score_dead_code_claims(GT_DEAD, GT_DEAD, GT_LIVE)
        assert result.total_score == pytest.approx(1.0)


# ── Fixture 2: Hidden callers (FP) → low score ──────────────────────────────

class TestHiddenCallersFalsePositive:
    def test_claiming_live_code_as_dead_penalized(self) -> None:
        """Claiming live code is dead should heavily reduce precision."""
        claimed = [
            _sym("src/utils.py", "unused_helper"),       # TP
            _sym("src/core.py", "process_request"),       # FP - has direct callers
            _sym("src/core.py", "handle_event"),          # FP - dynamic dispatch
        ]
        result = score_dead_code_claims(claimed, GT_DEAD, GT_LIVE)

        assert result.true_positives == 1
        assert result.false_positives == 2
        assert result.precision < 0.7
        assert result.total_score < 0.5

    def test_all_claims_are_live_code(self) -> None:
        """Claiming only live code as dead → near zero."""
        claimed = [
            _sym("src/core.py", "process_request"),
            _sym("src/plugins.py", "load_plugin"),
        ]
        result = score_dead_code_claims(claimed, GT_DEAD, GT_LIVE)

        assert result.true_positives == 0
        assert result.false_positives == 2
        assert result.precision == 0.0
        assert result.total_score == 0.0


# ── Fixture 3: Dynamic dispatch → flagged with lower confidence ──────────────

class TestDynamicDispatchFlagging:
    def test_dynamic_fp_less_penalized_than_direct_fp(self) -> None:
        """FP on dynamic-reachable code should be penalized less than direct FP."""
        # Claim with one direct FP
        claimed_direct_fp = [
            _sym("src/utils.py", "unused_helper"),   # TP
            _sym("src/core.py", "process_request"),   # FP - direct
        ]
        score_direct = score_dead_code_claims(claimed_direct_fp, GT_DEAD, GT_LIVE)

        # Claim with one dynamic FP
        claimed_dynamic_fp = [
            _sym("src/utils.py", "unused_helper"),   # TP
            _sym("src/core.py", "handle_event"),      # FP - dynamic
        ]
        score_dynamic = score_dead_code_claims(claimed_dynamic_fp, GT_DEAD, GT_LIVE)

        assert score_dynamic.dynamic_flags == 1
        assert score_direct.dynamic_flags == 0
        # Dynamic FP should yield higher score than direct FP
        assert score_dynamic.total_score > score_direct.total_score

    def test_reflection_fp_also_discounted(self) -> None:
        """Reflection-reachable FP also gets the dynamic discount."""
        claimed = [
            _sym("src/utils.py", "unused_helper"),   # TP
            _sym("src/plugins.py", "load_plugin"),    # FP - reflection
        ]
        result = score_dead_code_claims(claimed, GT_DEAD, GT_LIVE)
        assert result.dynamic_flags == 1
        assert result.total_score > 0.0


# ── Fixture 4: Empty submission → low score ──────────────────────────────────

class TestEmptySubmission:
    def test_empty_list_scores_zero(self) -> None:
        result = score_dead_code_claims([], GT_DEAD, GT_LIVE)
        assert result.total_score == 0.0
        assert result.true_positives == 0
        assert result.false_negatives == 4

    def test_empty_with_no_ground_truth(self) -> None:
        """Edge case: no dead code exists and agent submits nothing."""
        result = score_dead_code_claims([], [], GT_LIVE)
        assert result.total_score == 0.0


# ── Fixture 5: Partial identification → proportional score ───────────────────

class TestPartialIdentification:
    def test_half_dead_code_found(self) -> None:
        """Finding 2 of 4 dead symbols with no FPs → ~0.5 recall, 1.0 precision."""
        claimed = [
            _sym("src/utils.py", "unused_helper"),
            _sym("src/legacy.py", "deprecated_init"),
        ]
        result = score_dead_code_claims(claimed, GT_DEAD, GT_LIVE)

        assert result.true_positives == 2
        assert result.false_positives == 0
        assert result.false_negatives == 2
        assert result.precision == 1.0
        assert result.recall == pytest.approx(0.5)
        # With precision-weighted F-score (beta=0.5), high precision lifts score
        assert result.total_score > 0.5

    def test_one_of_four_found(self) -> None:
        """Finding 1 of 4 dead symbols → low recall, full precision."""
        claimed = [_sym("src/utils.py", "unused_helper")]
        result = score_dead_code_claims(claimed, GT_DEAD, GT_LIVE)

        assert result.true_positives == 1
        assert result.recall == pytest.approx(0.25)
        assert result.precision == 1.0
        assert 0.2 < result.total_score < 0.7

    def test_three_of_four_with_one_fp(self) -> None:
        """3 TP + 1 FP → decent but not great."""
        claimed = [
            _sym("src/utils.py", "unused_helper"),
            _sym("src/utils.py", "old_formatter"),
            _sym("src/legacy.py", "deprecated_init"),
            _sym("src/core.py", "process_request"),  # FP
        ]
        result = score_dead_code_claims(claimed, GT_DEAD, GT_LIVE)

        assert result.true_positives == 3
        assert result.false_positives == 1
        assert 0.4 < result.total_score < 0.85


# ── Fixture 6: Precision > recall weighting ──────────────────────────────────

class TestPrecisionWeighting:
    def test_high_precision_low_recall_beats_balanced(self) -> None:
        """With beta=0.5, high precision with lower recall should beat balanced."""
        # High precision: 2 TP, 0 FP, 2 FN
        high_prec = score_dead_code_claims(
            [_sym("src/utils.py", "unused_helper"), _sym("src/utils.py", "old_formatter")],
            GT_DEAD, GT_LIVE,
        )
        # Balanced but noisier: 3 TP, 2 FP, 1 FN
        balanced = score_dead_code_claims(
            [
                _sym("src/utils.py", "unused_helper"),
                _sym("src/utils.py", "old_formatter"),
                _sym("src/legacy.py", "deprecated_init"),
                _sym("src/core.py", "process_request"),   # FP
                _sym("src/config.py", "get_setting"),      # FP
            ],
            GT_DEAD, GT_LIVE,
        )
        # Precision-weighted favors the precise one
        assert high_prec.precision > balanced.precision
        assert high_prec.total_score > balanced.total_score


# ── Fixture 7: Plugin validate() integration ─────────────────────────────────

class TestCallGraphValidatorPlugin:
    def test_valid_workspace(self, tmp_path: Path) -> None:
        workspace = tmp_path / "workspace"
        workspace.mkdir()
        gt_dir = workspace / "ground_truth"
        gt_dir.mkdir()

        (workspace / "dead_code_report.json").write_text(json.dumps([
            _sym("src/utils.py", "unused_helper"),
            _sym("src/legacy.py", "deprecated_init"),
        ]))
        (gt_dir / "dead_code.json").write_text(json.dumps([
            _sym("src/utils.py", "unused_helper"),
            _sym("src/legacy.py", "deprecated_init"),
        ]))
        (gt_dir / "live_code.json").write_text(json.dumps([
            _sym("src/core.py", "main", reachability="direct"),
        ]))

        validator = CallGraphValidator()
        result = validator.validate(workspace)
        assert result.valid is True
        assert "score=1.000" in result.detail

    def test_missing_report(self, tmp_path: Path) -> None:
        workspace = tmp_path / "workspace"
        workspace.mkdir()
        validator = CallGraphValidator()
        result = validator.validate(workspace)
        assert result.valid is False
        assert "No dead_code_report.json" in result.detail

    def test_root_decoy_shadowing_pinned_report_is_ambiguous(self, tmp_path: Path) -> None:
        # A workspace-root decoy alongside the real report in a repo subdir must
        # NOT be silently graded as candidates[0]; reject as ambiguous. The
        # sealed ground_truth/ reads stay direct and are unaffected.
        workspace = tmp_path / "workspace"
        workspace.mkdir()
        gt_dir = workspace / "ground_truth"
        gt_dir.mkdir()
        (gt_dir / "dead_code.json").write_text(json.dumps([
            _sym("src/utils.py", "unused_helper"),
        ]))
        (gt_dir / "live_code.json").write_text(json.dumps([
            _sym("src/core.py", "main", reachability="direct"),
        ]))
        (workspace / "dead_code_report.json").write_text(json.dumps([]))  # decoy
        repo = workspace / "react"
        repo.mkdir()
        (repo / "dead_code_report.json").write_text(json.dumps([
            _sym("src/utils.py", "unused_helper"),
        ]))
        result = CallGraphValidator().validate(workspace)
        assert result.valid is False
        assert "ambiguous" in result.detail.lower()

    def test_invalid_json_report(self, tmp_path: Path) -> None:
        workspace = tmp_path / "workspace"
        workspace.mkdir()
        (workspace / "dead_code_report.json").write_text("not json{")
        validator = CallGraphValidator()
        result = validator.validate(workspace)
        assert result.valid is False
        assert "Invalid report JSON" in result.detail

    def test_missing_ground_truth(self, tmp_path: Path) -> None:
        workspace = tmp_path / "workspace"
        workspace.mkdir()
        (workspace / "dead_code_report.json").write_text(json.dumps([
            _sym("src/a.py", "foo"),
        ]))
        validator = CallGraphValidator()
        result = validator.validate(workspace)
        assert result.valid is False
        assert "Ground truth" in result.detail

    def test_report_must_be_array(self, tmp_path: Path) -> None:
        workspace = tmp_path / "workspace"
        workspace.mkdir()
        gt_dir = workspace / "ground_truth"
        gt_dir.mkdir()
        (workspace / "dead_code_report.json").write_text(json.dumps({"bad": "format"}))
        (gt_dir / "dead_code.json").write_text(json.dumps([]))
        (gt_dir / "live_code.json").write_text(json.dumps([]))
        validator = CallGraphValidator()
        result = validator.validate(workspace)
        assert result.valid is False
        assert "must be a JSON array" in result.detail

    def test_plugin_registered(self) -> None:
        from eb_verify.plugins import get_validator
        v = get_validator("call_graph")
        assert v is not None
        assert isinstance(v, CallGraphValidator)
