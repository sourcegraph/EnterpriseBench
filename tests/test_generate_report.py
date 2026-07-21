"""Tests for scripts/generate_report.py."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
import sys

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))

from generate_report import (
    ReportInputs,
    build_calibration_section,
    build_cost_section,
    build_coverage_section,
    build_executive_summary,
    build_key_findings,
    build_mcp_impact,
    build_recommendations,
    build_reproducibility_section,
    build_score_by_mode,
    build_score_by_suite,
    generate_report,
    load_inputs,
    make_table,
    _chart_ref,
    _cohens_d_interpretation,
    _count_all_modes_tasks,
    _delta_modes_present,
    _fmt,
    _mode_label,
    _pct,
)

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

MINIMAL_SCORE_ANALYSIS: dict = {
    "generated_at": "2026-04-01T00:00:00+00:00",
    "total_results": 10,
    "by_mode": {
        "baseline": {
            "count": 8,
            "mean": 0.75,
            "median": 0.80,
            "std": 0.15,
            "min": 0.2,
            "max": 1.0,
            "pass_rate": 0.625,
        },
        "hybrid": {
            "count": 2,
            "mean": 0.90,
            "median": 0.90,
            "std": 0.10,
            "min": 0.80,
            "max": 1.0,
            "pass_rate": 0.50,
        },
    },
    "by_suite": {
        "customer_escalation": {
            "baseline": {"count": 5, "mean": 0.70, "pass_rate": 0.60},
        },
        "feature_delivery": {
            "baseline": {"count": 3, "mean": 0.82, "pass_rate": 0.67},
            "hybrid": {"count": 2, "mean": 0.90, "pass_rate": 0.50},
        },
    },
    "by_difficulty": {
        "medium": {
            "baseline": {"count": 6, "mean": 0.72, "pass_rate": 0.50},
            "hybrid": {"count": 2, "mean": 0.90, "pass_rate": 0.50},
        },
        "hard": {
            "baseline": {"count": 2, "mean": 0.80, "pass_rate": 0.50},
        },
    },
    "by_task_type": {
        "error_provenance": {
            "baseline": {"count": 4, "mean": 0.68, "pass_rate": 0.50},
        },
        "api_contract": {
            "baseline": {"count": 4, "mean": 0.82, "pass_rate": 0.75},
            "hybrid": {"count": 2, "mean": 0.90, "pass_rate": 0.50},
        },
    },
    "mcp_delta": {
        "hybrid_vs_baseline": {
            "n_paired": 2,
            "mean_delta": 0.05,
            "median_delta": 0.04,
            "pct_improved": 0.50,
            "pct_degraded": 0.0,
            "pct_unchanged": 0.50,
            "cohens_d": 0.35,
            "wilcoxon_p": None,
            "significant": False,
            "note": "Too few pairs",
        },
    },
    "calibration_bias": {
        "calibration_task_count": 4,
        "mean_by_mode": {"baseline": 0.70, "hybrid": 0.72},
        "max_mode_delta": 0.02,
        "bias_flagged": False,
        "bias_threshold": 0.1,
    },
    "per_task": [
        {
            "task_id": "t1",
            "suite": "customer_escalation",
            "difficulty": "medium",
            "task_type": "error_provenance",
            "scores": {"baseline": 0.70, "hybrid": 0.80, "mcp_only": 0.60},
            "is_calibration": False,
        },
        {
            "task_id": "t2",
            "suite": "feature_delivery",
            "difficulty": "hard",
            "task_type": "api_contract",
            "scores": {"baseline": 0.90},
            "is_calibration": False,
        },
    ],
}


# Publish matrix: baseline/mcp_only/cli, no hybrid — the shape bead k1nud
# exists to make the reporting layer handle without dropping/mislabeling cli.
CLI_ARM_SCORE_ANALYSIS: dict = {
    "generated_at": "2026-04-01T00:00:00+00:00",
    "total_results": 15,
    "by_mode": {
        "baseline": {"count": 5, "mean": 0.60, "median": 0.60, "std": 0.10, "pass_rate": 0.40},
        "mcp_only": {"count": 5, "mean": 0.70, "median": 0.70, "std": 0.10, "pass_rate": 0.60},
        "cli": {"count": 5, "mean": 0.78, "median": 0.80, "std": 0.08, "pass_rate": 0.80},
    },
    "by_suite": {
        "customer_escalation": {
            "baseline": {"count": 5, "mean": 0.60, "pass_rate": 0.40},
        },
        "feature_delivery": {
            "baseline": {"count": 5, "mean": 0.60, "pass_rate": 0.40},
            "mcp_only": {"count": 5, "mean": 0.70, "pass_rate": 0.60},
            "cli": {"count": 5, "mean": 0.78, "pass_rate": 0.80},
        },
    },
    "by_difficulty": {},
    "by_task_type": {},
    "mcp_delta": {
        "mcp_only_vs_baseline": {
            "n_paired": 5,
            "mean_delta": 0.10,
            "median_delta": 0.10,
            "pct_improved": 0.6,
            "pct_degraded": 0.0,
            "pct_unchanged": 0.4,
            "cohens_d": 0.4,
            "wilcoxon_p": None,
            "significant": False,
        },
        "cli_vs_baseline": {
            "n_paired": 5,
            "mean_delta": 0.18,
            "median_delta": 0.20,
            "pct_improved": 0.8,
            "pct_degraded": 0.0,
            "pct_unchanged": 0.2,
            "cohens_d": 0.7,
            "wilcoxon_p": None,
            "significant": False,
        },
    },
    "calibration_bias": {
        "calibration_task_count": 0,
        "mean_by_mode": {},
        "max_mode_delta": None,
        "bias_flagged": False,
        "bias_threshold": 0.1,
    },
    "per_task": [
        {
            "task_id": "t1",
            "suite": "feature_delivery",
            "difficulty": "medium",
            "task_type": "api_contract",
            "scores": {"baseline": 0.60, "mcp_only": 0.70, "cli": 0.80},
            "is_calibration": False,
        },
    ],
}


def _make_inputs(
    score_analysis: dict | None = None,
    cost_report: dict | None = None,
    reproducibility_report: dict | None = None,
    available_charts: frozenset[str] | None = None,
) -> ReportInputs:
    return ReportInputs(
        score_analysis=score_analysis or MINIMAL_SCORE_ANALYSIS,
        cost_report=cost_report,
        reproducibility_report=reproducibility_report,
        charts_dir=None,
        available_charts=available_charts or frozenset(),
    )


# ---------------------------------------------------------------------------
# Table helpers
# ---------------------------------------------------------------------------


class TestMakeTable:
    def test_basic_table(self) -> None:
        result = make_table(["A", "B"], [["1", "2"], ["3", "4"]])
        assert "| A | B |" in result
        assert "| 1 | 2 |" in result
        assert "| --- | --- |" in result

    def test_empty_rows(self) -> None:
        result = make_table(["A"], [])
        assert "No data" in result

    def test_single_row(self) -> None:
        result = make_table(["X"], [["val"]])
        lines = result.strip().split("\n")
        assert len(lines) == 3  # header, separator, data


class TestFormatHelpers:
    def test_fmt_float(self) -> None:
        assert _fmt(0.12345) == "0.1235"
        assert _fmt(0.5, decimals=2) == "0.50"

    def test_fmt_none(self) -> None:
        assert _fmt(None) == "N/A"

    def test_fmt_int(self) -> None:
        assert _fmt(42) == "42"

    def test_pct(self) -> None:
        assert _pct(0.625) == "62.5%"
        assert _pct(None) == "N/A"
        assert _pct(1.0) == "100.0%"

    def test_cohens_d_interpretation(self) -> None:
        assert _cohens_d_interpretation(0.1) == "negligible"
        assert _cohens_d_interpretation(0.3) == "small"
        assert _cohens_d_interpretation(0.6) == "medium"
        assert _cohens_d_interpretation(1.2) == "large"
        assert _cohens_d_interpretation(-0.7) == "medium"
        assert _cohens_d_interpretation(None) == "N/A"


# ---------------------------------------------------------------------------
# Section tests
# ---------------------------------------------------------------------------


class TestExecutiveSummary:
    def test_contains_task_count(self) -> None:
        result = build_executive_summary(_make_inputs())
        assert "10 task results" in result

    def test_contains_pass_rate(self) -> None:
        result = build_executive_summary(_make_inputs())
        assert "62.5%" in result

    def test_contains_mcp_delta_direction(self) -> None:
        result = build_executive_summary(_make_inputs())
        assert "positive" in result

    def test_calibration_pass(self) -> None:
        result = build_executive_summary(_make_inputs())
        assert "PASS" in result

    def test_calibration_flagged(self) -> None:
        sa = {**MINIMAL_SCORE_ANALYSIS}
        sa["calibration_bias"] = {**sa["calibration_bias"], "bias_flagged": True}
        result = build_executive_summary(_make_inputs(score_analysis=sa))
        assert "WARNING" in result


class TestCoverageSection:
    def test_mode_counts(self) -> None:
        result = build_coverage_section(_make_inputs())
        assert "8 tasks scored" in result
        assert "2 tasks scored" in result

    def test_all_three_modes_count(self) -> None:
        result = build_coverage_section(_make_inputs())
        assert "Tasks with all 3 modes: 1" in result


class TestCountAllModesTasks:
    def test_all_modes_present(self) -> None:
        tasks = [
            {"scores": {"baseline": 0.5, "hybrid": 0.6, "mcp_only": 0.7}},
            {"scores": {"baseline": 0.5}},
        ]
        assert _count_all_modes_tasks(tasks) == 1

    def test_none_score_excluded(self) -> None:
        tasks = [
            {"scores": {"baseline": 0.5, "hybrid": None, "mcp_only": 0.7}},
        ]
        assert _count_all_modes_tasks(tasks) == 0


class TestScoreByMode:
    def test_table_present(self) -> None:
        result = build_score_by_mode(_make_inputs())
        assert "| Mode |" in result
        assert "baseline" in result
        assert "hybrid" in result


class TestScoreBySuite:
    def test_table_present(self) -> None:
        result = build_score_by_suite(_make_inputs())
        assert "customer_escalation" in result
        assert "feature_delivery" in result


class TestMCPImpact:
    def test_delta_shown(self) -> None:
        result = build_mcp_impact(_make_inputs())
        assert "Hybrid vs Baseline" in result
        assert "Paired tasks: 2" in result
        assert "improvement" in result

    def test_insufficient_data_note(self) -> None:
        result = build_mcp_impact(_make_inputs())
        assert "Insufficient paired data" in result

    def test_no_delta_data(self) -> None:
        sa = {**MINIMAL_SCORE_ANALYSIS, "mcp_delta": {}}
        result = build_mcp_impact(_make_inputs(score_analysis=sa))
        assert "No MCP delta data" in result


class TestCalibrationSection:
    def test_pass_status(self) -> None:
        result = build_calibration_section(_make_inputs())
        assert "**PASS**" in result
        assert "not biased" in result

    def test_fail_status(self) -> None:
        sa = {**MINIMAL_SCORE_ANALYSIS}
        sa["calibration_bias"] = {**sa["calibration_bias"], "bias_flagged": True}
        result = build_calibration_section(_make_inputs(score_analysis=sa))
        assert "**FAIL**" in result


class TestReproducibilitySection:
    def test_missing_report(self) -> None:
        result = build_reproducibility_section(_make_inputs())
        assert "not yet generated" in result

    def test_present_report(self) -> None:
        repro = {
            "sample_size": 25,
            "variance_threshold": 0.15,
            "tasks_with_multiple_runs": 18,
            "tasks_flagged": 3,
            "mean_variance": 0.04,
            "pass": True,
        }
        result = build_reproducibility_section(
            _make_inputs(reproducibility_report=repro)
        )
        assert "25" in result
        assert "18" in result
        assert "**PASS**" in result


class TestCostSection:
    def test_missing_report(self) -> None:
        result = build_cost_section(_make_inputs())
        assert "not yet generated" in result

    def test_present_report(self) -> None:
        cost = {
            "total_cost_usd": 123.45,
            "total_tasks": 50,
            "by_mode": {
                "baseline": {"count": 30, "total_cost": 80.0, "avg_cost": 2.67},
                "hybrid": {"count": 20, "total_cost": 43.45, "avg_cost": 2.17},
            },
        }
        result = build_cost_section(_make_inputs(cost_report=cost))
        assert "$123.45" in result
        assert "baseline" in result
        assert "Average cost per task" in result

    def test_latency_shown_next_to_cost(self) -> None:
        cost = {
            "total_cost_usd": 10.0,
            "total_tasks": 5,
            "by_mode": {
                "baseline": {
                    "count": 5,
                    "total_cost": 10.0,
                    "avg_cost": 2.0,
                    "latency_seconds": {"count": 5, "mean": 90.0, "median": 85.0, "p90": 120.0},
                },
            },
        }
        result = build_cost_section(_make_inputs(cost_report=cost))
        assert "Median Latency" in result
        assert "85.0s" in result
        assert "120.0s" in result

    def test_latency_missing_shows_na(self) -> None:
        cost = {
            "total_cost_usd": 10.0,
            "total_tasks": 5,
            "by_mode": {"baseline": {"count": 5, "total_cost": 10.0, "avg_cost": 2.0}},
        }
        result = build_cost_section(_make_inputs(cost_report=cost))
        assert "N/A" in result


class TestKeyFindings:
    def test_has_findings(self) -> None:
        result = build_key_findings(_make_inputs())
        assert "Highest-scoring suite" in result
        assert "Lowest-scoring suite" in result

    def test_mcp_direction_noted(self) -> None:
        result = build_key_findings(_make_inputs())
        assert "positive mean delta" in result

    def test_missing_mcp_suites(self) -> None:
        result = build_key_findings(_make_inputs())
        assert "customer_escalation" in result  # has no MCP modes


class TestRecommendations:
    def test_sweep_recommendation(self) -> None:
        result = build_recommendations(_make_inputs())
        assert "Complete the sweep" in result

    def test_missing_mcp_recommendation(self) -> None:
        result = build_recommendations(_make_inputs())
        assert "customer_escalation" in result

    def test_repro_recommendation(self) -> None:
        result = build_recommendations(_make_inputs())
        assert "repeat runs" in result


# ---------------------------------------------------------------------------
# Chart references
# ---------------------------------------------------------------------------


class TestCliArmDataDriven:
    """bead k1nud: the report must surface cli, not drop or mislabel it."""

    def _inputs(self) -> ReportInputs:
        return _make_inputs(score_analysis=CLI_ARM_SCORE_ANALYSIS)

    def test_mode_label(self) -> None:
        assert _mode_label("cli") == "CLI"
        assert _mode_label("mcp_only") == "MCP-only"
        assert _mode_label("hybrid") == "Hybrid"

    def test_delta_modes_present_excludes_absent_hybrid(self) -> None:
        modes = _delta_modes_present(CLI_ARM_SCORE_ANALYSIS["mcp_delta"])
        assert modes == ["mcp_only", "cli"]

    def test_executive_summary_mentions_cli(self) -> None:
        result = build_executive_summary(self._inputs())
        assert "CLI vs baseline" in result

    def test_score_by_mode_table_has_cli_row(self) -> None:
        result = build_score_by_mode(self._inputs())
        assert "cli" in result

    def test_mcp_impact_has_cli_block_not_hybrid(self) -> None:
        result = build_mcp_impact(self._inputs())
        assert "CLI vs Baseline" in result
        assert "Hybrid vs Baseline" not in result

    def test_key_findings_mentions_cli(self) -> None:
        result = build_key_findings(self._inputs())
        assert "CLI mode shows" in result

    def test_key_findings_no_data_gap_for_customer_escalation_suite(self) -> None:
        """customer_escalation has only baseline -> missing-MCP finding, driven
        by whatever non-baseline arms are actually in by_mode (mcp_only + cli),
        not a hard-coded {hybrid, mcp_only} check."""
        result = build_key_findings(self._inputs())
        assert "customer_escalation" in result

    def test_recommendations_reference_cli_coverage(self) -> None:
        result = build_recommendations(self._inputs())
        assert "customer_escalation" in result


class TestChartReferences:
    def test_chart_included_when_exists(self) -> None:
        result = _chart_ref(
            "score_by_mode.png", "Score by Mode", frozenset({"score_by_mode.png"})
        )
        assert "![Score by Mode](charts/score_by_mode.png)" in result

    def test_chart_excluded_when_missing(self) -> None:
        result = _chart_ref("score_by_mode.png", "Score by Mode", frozenset())
        assert result == ""

    def test_chart_in_full_report(self) -> None:
        inputs = _make_inputs(
            available_charts=frozenset({"score_by_mode.png", "score_heatmap.png"})
        )
        report = generate_report(inputs)
        assert "![Score by Mode](charts/score_by_mode.png)" in report
        assert "![Score Heatmap](charts/score_heatmap.png)" in report
        # Charts that don't exist should not appear
        assert "mcp_delta.png" not in report


# ---------------------------------------------------------------------------
# Full report integration
# ---------------------------------------------------------------------------


class TestFullReport:
    def test_all_sections_present(self) -> None:
        report = generate_report(_make_inputs())
        required_headings = [
            "# EnterpriseBench Analysis Report",
            "## Executive Summary",
            "## 1. Coverage",
            "## 2. Score Distributions",
            "## 3. MCP Impact Analysis",
            "## 4. Calibration Bias Check",
            "## 5. Reproducibility",
            "## 6. Cost Analysis",
            "## 7. Key Findings",
            "## 8. Recommendations",
        ]
        for heading in required_headings:
            assert heading in report, f"Missing section: {heading}"

    def test_generated_date(self) -> None:
        report = generate_report(_make_inputs())
        assert "2026-04-01" in report

    def test_missing_optional_inputs_graceful(self) -> None:
        """No cost or reproducibility -> placeholders, no crash."""
        report = generate_report(_make_inputs())
        assert "not yet generated" in report  # both missing

    def test_with_all_optional_inputs(self) -> None:
        cost = {
            "total_cost_usd": 50.0,
            "total_tasks": 10,
            "by_mode": {"baseline": {"count": 10, "total_cost": 50.0, "avg_cost": 5.0}},
        }
        repro = {
            "sample_size": 10,
            "variance_threshold": 0.15,
            "tasks_with_multiple_runs": 8,
            "tasks_flagged": 1,
            "mean_variance": 0.03,
            "pass": True,
        }
        inputs = _make_inputs(cost_report=cost, reproducibility_report=repro)
        report = generate_report(inputs)
        assert "$50.00" in report
        assert "**PASS**" in report


# ---------------------------------------------------------------------------
# File I/O integration
# ---------------------------------------------------------------------------


class TestLoadInputs:
    def test_missing_score_analysis_raises(self, tmp_path: Path) -> None:
        with pytest.raises(FileNotFoundError):
            load_inputs(
                analysis_path=tmp_path / "nonexistent.json",
                cost_path=tmp_path / "cost.json",
                repro_path=tmp_path / "repro.json",
                charts_dir=tmp_path / "charts",
            )

    def test_load_minimal(self, tmp_path: Path) -> None:
        analysis = tmp_path / "score_analysis.json"
        analysis.write_text(json.dumps(MINIMAL_SCORE_ANALYSIS))
        inputs = load_inputs(
            analysis_path=analysis,
            cost_path=tmp_path / "cost.json",
            repro_path=tmp_path / "repro.json",
            charts_dir=tmp_path / "charts",
        )
        assert inputs.score_analysis["total_results"] == 10
        assert inputs.cost_report is None
        assert inputs.reproducibility_report is None
        assert inputs.available_charts == frozenset()

    def test_load_with_charts(self, tmp_path: Path) -> None:
        analysis = tmp_path / "score_analysis.json"
        analysis.write_text(json.dumps(MINIMAL_SCORE_ANALYSIS))
        charts = tmp_path / "charts"
        charts.mkdir()
        (charts / "score_by_mode.png").write_bytes(b"\x89PNG")
        (charts / "readme.txt").write_text("ignore")

        inputs = load_inputs(
            analysis_path=analysis,
            cost_path=tmp_path / "cost.json",
            repro_path=tmp_path / "repro.json",
            charts_dir=charts,
        )
        assert "score_by_mode.png" in inputs.available_charts
        assert "readme.txt" not in inputs.available_charts
