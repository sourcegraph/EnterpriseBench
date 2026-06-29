"""Tests for the MCP-lift dose-response analysis (EnterpriseBench-wgma).

Validates the deterministic statistical primitives (Jonckheere–Terpstra trend,
percentile bootstrap, paired Cohen's d_z) and the task.toml join — including the
archived-task provenance path, which is load-bearing for the ``low`` stratum.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_ROOT / "scripts" / "analysis"))

import mcp_lift_doseresponse as dr  # noqa: E402


# ---------------------------------------------------------------------------
# Jonckheere–Terpstra
# ---------------------------------------------------------------------------


class TestJonckheereTerpstra:
    def test_perfect_increasing_trend_is_significant(self):
        # Cleanly separated, strictly increasing groups: strong upward trend.
        groups = [[1, 2, 3], [4, 5, 6], [7, 8, 9]]
        res = dr.jonckheere_terpstra(groups)
        assert res["applicable"]
        assert res["z"] > 0
        assert res["p_increasing_one_sided"] < 0.05

    def test_j_statistic_matches_brute_force_definition(self):
        # J = sum over i<j of #{y in g_j > x in g_i} (+0.5 per tie).
        groups = [[1, 3], [2, 4]]
        # pairs across (g0,g1): (1,2)>,(1,4)>,(3,2)no,(3,4)> => 3
        expected_j = 3.0
        res = dr.jonckheere_terpstra(groups)
        assert res["j_statistic"] == expected_j
        # Under H0, E[J] = (N^2 - sum n_i^2)/4 = (16 - 8)/4 = 2.0
        assert res["expected_j_under_h0"] == 2.0

    def test_decreasing_trend_gives_pvalue_above_half(self):
        groups = [[7, 8, 9], [4, 5, 6], [1, 2, 3]]
        res = dr.jonckheere_terpstra(groups)
        assert res["z"] < 0
        assert res["p_increasing_one_sided"] > 0.5

    def test_ties_counted_as_half(self):
        # All identical => every cross-pair is a tie worth 0.5.
        groups = [[5, 5], [5, 5]]
        res = dr.jonckheere_terpstra(groups)
        # 2x2 = 4 cross-pairs, each 0.5 => J = 2.0, exactly E[J].
        assert res["j_statistic"] == 2.0
        assert res["z"] == pytest.approx(0.0, abs=1e-9)
        assert res["p_increasing_one_sided"] == pytest.approx(0.5, abs=1e-6)

    def test_fewer_than_two_groups_not_applicable(self):
        assert dr.jonckheere_terpstra([[1, 2, 3]])["applicable"] is False
        assert dr.jonckheere_terpstra([[1, 2], []])["applicable"] is False


# ---------------------------------------------------------------------------
# Bootstrap CI + Cohen's d_z
# ---------------------------------------------------------------------------


class TestBootstrapCI:
    def test_deterministic_for_fixed_seed(self):
        deltas = [-0.2, 0.1, 0.0, -0.5, 0.3, -0.1, 0.0, 0.4]
        a = dr.bootstrap_ci(deltas, iters=1000, seed=7)
        b = dr.bootstrap_ci(deltas, iters=1000, seed=7)
        assert a == b

    def test_ci_brackets_the_mean(self):
        deltas = [0.1, 0.2, 0.15, 0.05, 0.12, 0.18]
        lo, hi = dr.bootstrap_ci(deltas, iters=2000, seed=1)
        mean = sum(deltas) / len(deltas)
        assert lo <= mean <= hi

    def test_too_few_points_returns_none(self):
        assert dr.bootstrap_ci([0.1], iters=100, seed=1) is None


class TestCohensDz:
    def test_zero_variance_returns_zero(self):
        assert dr.cohens_dz([0.3, 0.3, 0.3]) == 0.0

    def test_known_value(self):
        # mean=0.2, stdev(sample)=0.1 => d_z = 2.0
        assert dr.cohens_dz([0.1, 0.2, 0.3]) == pytest.approx(2.0, abs=1e-9)

    def test_single_point_undefined(self):
        assert dr.cohens_dz([0.5]) is None


# ---------------------------------------------------------------------------
# Join + stratum summary + negative control
# ---------------------------------------------------------------------------


def _make_toml(path: Path, task_id: str, benefit: str, stratum: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        f'difficulty_stratum = "{stratum}"\n\n'
        f'[task]\nid = "{task_id}"\n\n'
        f'[tool_access]\nexpected_mcp_benefit = "{benefit}"\n'
    )


class TestJoinAndProvenance:
    def test_index_keys_by_inner_id_and_flags_archived(self, tmp_path: Path):
        root = tmp_path / "benchmarks"
        _make_toml(root / "live-dir" / "task.toml", "live-001", "high", "dual_repo")
        _make_toml(
            root / "_archived" / "old-dir" / "task.toml",
            "arch-001",
            "low",
            "calibration",
        )
        index = dr.index_task_tomls(root)
        # Keyed by [task].id, NOT the directory name.
        assert "live-001" in index and "live-dir" not in index
        assert index["live-001"][1] is False
        assert index["arch-001"][1] is True

    def test_paired_deltas_joins_and_skips_unjoinable(self, tmp_path: Path):
        root = tmp_path / "benchmarks"
        _make_toml(root / "a" / "task.toml", "t-low", "low", "calibration")
        _make_toml(root / "b" / "task.toml", "t-high", "high", "dual_repo")
        index = dr.index_task_tomls(root)

        per_task = [
            {"task_id": "t-low", "scores": {"baseline": 0.8, "mcp_only": 0.5},
             "is_calibration": True},
            {"task_id": "t-high", "scores": {"baseline": 0.4, "mcp_only": 0.9},
             "is_calibration": False},
            {"task_id": "t-missing-toml", "scores": {"baseline": 0.5, "mcp_only": 0.5}},
            {"task_id": "t-no-mcp", "scores": {"baseline": 0.5}},
        ]
        records, warnings = dr.paired_deltas(per_task, index, "mcp_only")

        by_id = {r["task_id"]: r for r in records}
        assert set(by_id) == {"t-low", "t-high"}
        assert by_id["t-low"]["delta"] == pytest.approx(-0.3)
        assert by_id["t-high"]["delta"] == pytest.approx(0.5)
        assert by_id["t-low"]["expected_mcp_benefit"] == "low"
        # The two unjoinable tasks are warned about, never silently dropped.
        assert len(warnings) == 2
        assert any("t-missing-toml" in w for w in warnings)
        assert any("t-no-mcp" in w for w in warnings)

    def test_stratum_summary_orders_levels_and_counts(self, tmp_path: Path):
        records = [
            {"delta": -0.1, "expected_mcp_benefit": "low"},
            {"delta": 0.0, "expected_mcp_benefit": "medium"},
            {"delta": 0.2, "expected_mcp_benefit": "medium"},
            {"delta": 0.5, "expected_mcp_benefit": "high"},
        ]
        summary = dr.stratum_summary(
            records, "expected_mcp_benefit", dr.BENEFIT_ORDER, iters=500, seed=3
        )
        assert [s["level"] for s in summary] == ["low", "medium", "high"]
        assert [s["n"] for s in summary] == [1, 2, 1]
        assert summary[1]["mean_delta"] == pytest.approx(0.1)
