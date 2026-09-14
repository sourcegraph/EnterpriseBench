"""Tests for scripts/analysis/power_analysis.py."""

from __future__ import annotations

import json
import math
from pathlib import Path

import pytest

from scripts.analysis.power_analysis import (
    FullAnalysis,
    PowerResult,
    StratifiedPlan,
    _paired_ttest_power,
    build_stratified_plan,
    generate_methodology_section,
    load_pilot_csv,
    power_paired_ttest,
    power_wilcoxon_simulation,
    run_analysis,
    summarize_pilot,
    TOTAL_TASKS,
)

FIXTURE_CSV = Path(__file__).parent / "fixtures" / "pilot_scores.csv"


# ---------------------------------------------------------------------------
# Pilot data loading and summary
# ---------------------------------------------------------------------------


class TestLoadPilotCsv:
    def test_loads_all_tasks(self) -> None:
        data = load_pilot_csv(FIXTURE_CSV)
        assert len(data) == 4

    def test_loads_all_modes(self) -> None:
        data = load_pilot_csv(FIXTURE_CSV)
        for task_id, modes in data.items():
            assert set(modes.keys()) == {"baseline", "mcp_only", "hybrid"}, (
                f"Missing mode for {task_id}"
            )

    def test_three_reps_per_cell(self) -> None:
        data = load_pilot_csv(FIXTURE_CSV)
        for task_id, modes in data.items():
            for mode, scores in modes.items():
                assert len(scores) == 3, (
                    f"{task_id}/{mode} has {len(scores)} reps"
                )

    def test_scores_in_range(self) -> None:
        data = load_pilot_csv(FIXTURE_CSV)
        for task_id, modes in data.items():
            for mode, scores in modes.items():
                for s in scores:
                    assert 0.0 <= s <= 1.0, (
                        f"{task_id}/{mode}: score {s} out of range"
                    )


class TestSummarizePilot:
    @pytest.fixture
    def pilot(self):
        data = load_pilot_csv(FIXTURE_CSV)
        return summarize_pilot(data)

    def test_n_tasks(self, pilot) -> None:
        assert pilot.n_tasks == 4

    def test_n_reps(self, pilot) -> None:
        assert pilot.n_reps_per_cell == 3

    def test_differences_length(self, pilot) -> None:
        assert len(pilot.differences) == 4

    def test_mean_difference_positive(self, pilot) -> None:
        # Overall pilot shows slight positive effect
        assert pilot.mean_difference > 0

    def test_cohens_dz_positive(self, pilot) -> None:
        assert pilot.cohens_dz is not None
        assert pilot.cohens_dz > 0

    def test_sd_difference_positive(self, pilot) -> None:
        assert pilot.sd_difference > 0

    def test_config_drift_positive_discrimination(self, pilot) -> None:
        idx = pilot.task_ids.index("config-drift-argocd-redis-ha-004")
        assert pilot.differences[idx] > 0.3

    def test_incident_inv_negative_discrimination(self, pilot) -> None:
        idx = pilot.task_ids.index("incident-inv-docker-shutdown-004")
        assert pilot.differences[idx] < 0


# ---------------------------------------------------------------------------
# Paired t-test power
# ---------------------------------------------------------------------------


class TestPairedTtestPower:
    def test_power_increases_with_n(self) -> None:
        dz = 0.5
        powers = [_paired_ttest_power(dz, n, 0.1) for n in [10, 30, 50, 100]]
        for i in range(len(powers) - 1):
            assert powers[i] < powers[i + 1]

    def test_power_approaches_one_for_large_n(self) -> None:
        power = _paired_ttest_power(0.5, 200, 0.1)
        assert power > 0.99

    def test_power_low_for_small_n(self) -> None:
        power = _paired_ttest_power(0.3, 5, 0.1)
        assert power < 0.5

    def test_required_n_for_medium_effect(self) -> None:
        req_n, _ = power_paired_ttest(0.5, alpha=0.1, target_power=0.8)
        assert req_n is not None
        assert 15 <= req_n <= 40

    def test_required_n_none_for_negative_effect(self) -> None:
        req_n, power_112 = power_paired_ttest(-0.5, alpha=0.1, target_power=0.8)
        assert req_n is None
        assert power_112 is not None


# ---------------------------------------------------------------------------
# Wilcoxon simulation
# ---------------------------------------------------------------------------


class TestWilcoxonSimulation:
    def test_required_n_reasonable(self) -> None:
        req_n, _ = power_wilcoxon_simulation(
            0.1, 0.2, alpha=0.1, target_power=0.8, n_simulations=1_000
        )
        # Should need more than the t-test (less powerful)
        assert req_n is not None
        assert req_n >= 10

    def test_power_at_112_high(self) -> None:
        _, pow_112 = power_wilcoxon_simulation(
            0.1, 0.2, alpha=0.1, target_power=0.8, n_simulations=1_000
        )
        assert pow_112 is not None
        assert pow_112 > 0.9


# ---------------------------------------------------------------------------
# Stratified sampling plan
# ---------------------------------------------------------------------------


class TestStratifiedPlan:
    def test_plan_covers_all_cells(self) -> None:
        plan = build_stratified_plan(50)
        assert len(plan.cells) == 26  # 26 non-empty suite/stratum combos

    def test_allocations_do_not_exceed_available(self) -> None:
        plan = build_stratified_plan(50)
        for cell in plan.cells:
            assert cell.allocated <= cell.available

    def test_minimum_one_per_cell(self) -> None:
        plan = build_stratified_plan(30)
        for cell in plan.cells:
            assert cell.allocated >= 1

    def test_plan_when_n_exceeds_total(self) -> None:
        plan = build_stratified_plan(200)
        assert "exceeds" in plan.coverage_note
        for cell in plan.cells:
            assert cell.allocated <= cell.available

    def test_total_tasks_constant(self) -> None:
        assert TOTAL_TASKS == 113


# ---------------------------------------------------------------------------
# Full analysis
# ---------------------------------------------------------------------------


class TestRunAnalysis:
    @pytest.fixture
    def analysis(self):
        return run_analysis(FIXTURE_CSV, alpha=0.1, target_power=0.8, n_simulations=1_000)

    def test_returns_full_analysis(self, analysis) -> None:
        assert isinstance(analysis, FullAnalysis)

    def test_ttest_has_required_n(self, analysis) -> None:
        assert analysis.paired_ttest.required_n is not None
        assert analysis.paired_ttest.required_n > 0

    def test_wilcoxon_has_required_n(self, analysis) -> None:
        assert analysis.wilcoxon.required_n is not None
        assert analysis.wilcoxon.required_n > 0

    def test_recommendation_nonempty(self, analysis) -> None:
        assert len(analysis.recommendation) > 50

    def test_methodology_section(self, analysis) -> None:
        text = generate_methodology_section(analysis)
        assert "Power Analysis" in text
        assert "paired" in text.lower()
        assert "alpha" in text.lower()


class TestCli:
    def test_json_output_valid(self) -> None:
        from scripts.analysis.power_analysis import main
        import io
        import contextlib

        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            rc = main(["--pilot-csv", str(FIXTURE_CSV), "--json", "--simulations", "500"])
        assert rc == 0
        data = json.loads(buf.getvalue())
        assert "pilot" in data
        assert "paired_ttest" in data
        assert "wilcoxon" in data
        assert "stratified_plan" in data
