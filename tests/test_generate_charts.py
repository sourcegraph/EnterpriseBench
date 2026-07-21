"""Tests for scripts/generate_charts.py."""

from __future__ import annotations

import copy
import json
from pathlib import Path

import pytest

from scripts import generate_charts
from scripts.cost_tracker import SCHEMA_VERSION
from scripts.generate_charts import (
    MODE_ORDER,
    _delta_comparisons,
    _mode_color,
    _modes_present,
    chart_calibration_check,
    chart_cost_by_mode,
    chart_cost_per_task,
    chart_difficulty_distribution,
    chart_mcp_delta,
    chart_pass_rate_comparison,
    chart_per_task_scores,
    chart_score_by_mode,
    chart_score_heatmap,
    chart_task_type_scores,
    generate_all_charts,
)

# ── Fixtures ─────────────────────────────────────────────────────────────────

MINIMAL_DATA: dict = {
    "by_mode": {
        "baseline": {
            "count": 10,
            "mean": 0.70,
            "median": 0.75,
            "std": 0.20,
            "min": 0.0,
            "max": 1.0,
            "pass_rate": 0.50,
        },
        "hybrid": {
            "count": 3,
            "mean": 0.85,
            "median": 0.90,
            "std": 0.10,
            "min": 0.70,
            "max": 1.0,
            "pass_rate": 0.67,
        },
    },
    "by_suite": {
        "suite_a": {
            "baseline": {
                "count": 5,
                "mean": 0.65,
                "median": 0.7,
                "std": 0.15,
                "min": 0.3,
                "max": 1.0,
                "pass_rate": 0.4,
            },
            "hybrid": {
                "count": 2,
                "mean": 0.80,
                "median": 0.8,
                "std": 0.1,
                "min": 0.7,
                "max": 0.9,
                "pass_rate": 0.5,
            },
        },
        "suite_b": {
            "baseline": {
                "count": 5,
                "mean": 0.75,
                "median": 0.8,
                "std": 0.2,
                "min": 0.2,
                "max": 1.0,
                "pass_rate": 0.6,
            },
        },
    },
    "by_difficulty": {
        "medium": {
            "baseline": {
                "count": 6,
                "mean": 0.65,
                "median": 0.7,
                "std": 0.2,
                "min": 0.1,
                "max": 1.0,
                "pass_rate": 0.4,
            },
            "hybrid": {
                "count": 2,
                "mean": 0.80,
                "median": 0.8,
                "std": 0.1,
                "min": 0.7,
                "max": 0.9,
                "pass_rate": 0.5,
            },
        },
        "hard": {
            "baseline": {
                "count": 4,
                "mean": 0.75,
                "median": 0.8,
                "std": 0.15,
                "min": 0.3,
                "max": 1.0,
                "pass_rate": 0.5,
            },
        },
    },
    "by_task_type": {
        "error_provenance": {
            "baseline": {
                "count": 4,
                "mean": 0.70,
                "median": 0.7,
                "std": 0.2,
                "min": 0.2,
                "max": 1.0,
                "pass_rate": 0.5,
            },
            "hybrid": {
                "count": 1,
                "mean": 0.90,
                "median": 0.9,
                "std": 0.0,
                "min": 0.9,
                "max": 0.9,
                "pass_rate": 1.0,
            },
        },
        "config_drift": {
            "baseline": {
                "count": 3,
                "mean": 0.60,
                "median": 0.6,
                "std": 0.3,
                "min": 0.1,
                "max": 0.9,
                "pass_rate": 0.33,
            },
        },
    },
    "mcp_delta": {
        "hybrid_vs_baseline": {
            "n_paired": 2,
            "mean_delta": 0.10,
            "median_delta": 0.05,
            "pct_improved": 0.5,
            "pct_degraded": 0.0,
            "pct_unchanged": 0.5,
            "cohens_d": 0.5,
            "wilcoxon_p": None,
            "significant": False,
            "note": "Too few pairs",
        },
    },
    "calibration_bias": {
        "calibration_task_count": 2,
        "mean_by_mode": {"baseline": 0.60, "hybrid": 0.65},
        "max_mode_delta": 0.05,
        "bias_flagged": False,
        "bias_threshold": 0.1,
    },
    "per_task": [
        {
            "task_id": "task-001",
            "suite": "suite_a",
            "difficulty": "medium",
            "task_type": "error_provenance",
            "scores": {"baseline": 0.70, "hybrid": 0.85},
            "is_calibration": True,
        },
        {
            "task_id": "task-002",
            "suite": "suite_a",
            "difficulty": "hard",
            "task_type": "config_drift",
            "scores": {"baseline": 0.50},
            "is_calibration": False,
        },
        {
            "task_id": "task-003",
            "suite": "suite_b",
            "difficulty": "medium",
            "task_type": "error_provenance",
            "scores": {"baseline": 0.90, "hybrid": 0.80},
            "is_calibration": True,
        },
    ],
}

# The comparison view is what the charts render: one row per (task_id, mode),
# matched across arms. Note task-001 appears once per arm at DIFFERENT costs —
# keying the lookup on task_id alone collapses them (EnterpriseBench-jrgs).
MINIMAL_COST: dict = {
    "schema_version": SCHEMA_VERSION,
    "selection_rule": "test",
    "operational_economics": {
        "total_cost_usd": 30.0,
        "attempts": 8,
        "by_mode": {
            "baseline": {"attempts": 4, "total_cost_usd": 18.0,
                         "avg_cost_per_attempt": 4.5},
            "hybrid": {"attempts": 4, "total_cost_usd": 12.0,
                       "avg_cost_per_attempt": 3.0},
        },
    },
    "comparison_economics": {
        "total_cost_usd": 20.80,
        "tasks": 3,
        "modes": ["baseline", "hybrid"],
        "excluded_unmatched_task_ids": [],
        "by_mode": {
            "baseline": {"tasks": 3, "total_cost_usd": 4.30,
                         "avg_cost_per_task": 1.433333},
            "hybrid": {"tasks": 3, "total_cost_usd": 16.50,
                       "avg_cost_per_task": 5.50},
        },
        # task-003's hybrid run cost exactly $0.00 — a falsy value the old
        # `entry.get("cost_usd") or ...` lookup discarded instead of plotting.
        "per_cell": [
            {"task_id": "task-001", "mode": "baseline", "cost_usd": 1.50},
            {"task_id": "task-001", "mode": "hybrid", "cost_usd": 9.90},
            {"task_id": "task-002", "mode": "baseline", "cost_usd": 2.00},
            {"task_id": "task-002", "mode": "hybrid", "cost_usd": 6.60},
            {"task_id": "task-003", "mode": "baseline", "cost_usd": 0.80},
            {"task_id": "task-003", "mode": "hybrid", "cost_usd": 0.0},
        ],
    },
    "duplicate_attempts": [],
    "invalid_attempts": [],
}


@pytest.fixture
def analysis_file(tmp_path: Path) -> Path:
    p = tmp_path / "score_analysis.json"
    p.write_text(json.dumps(MINIMAL_DATA))
    return p


@pytest.fixture
def cost_file(tmp_path: Path) -> Path:
    p = tmp_path / "cost_report.json"
    p.write_text(json.dumps(MINIMAL_COST))
    return p


@pytest.fixture
def out_dir(tmp_path: Path) -> Path:
    d = tmp_path / "charts"
    d.mkdir()
    return d


# ── Individual chart tests ───────────────────────────────────────────────────


def _assert_png(path: Path) -> None:
    assert path.exists(), f"{path} was not created"
    assert path.stat().st_size > 0, f"{path} is empty"


def test_chart_score_by_mode(out_dir: Path) -> None:
    path = chart_score_by_mode(MINIMAL_DATA, out_dir)
    _assert_png(path)


def test_chart_score_heatmap(out_dir: Path) -> None:
    path = chart_score_heatmap(MINIMAL_DATA, out_dir)
    _assert_png(path)


def test_chart_difficulty_distribution(out_dir: Path) -> None:
    path = chart_difficulty_distribution(MINIMAL_DATA, out_dir)
    _assert_png(path)


def test_chart_task_type_scores(out_dir: Path) -> None:
    path = chart_task_type_scores(MINIMAL_DATA, out_dir)
    _assert_png(path)


def test_chart_mcp_delta(out_dir: Path) -> None:
    path = chart_mcp_delta(MINIMAL_DATA, out_dir)
    _assert_png(path)


def test_chart_pass_rate_comparison(out_dir: Path) -> None:
    path = chart_pass_rate_comparison(MINIMAL_DATA, out_dir)
    _assert_png(path)


def test_chart_per_task_scores(out_dir: Path) -> None:
    path = chart_per_task_scores(MINIMAL_DATA, out_dir)
    _assert_png(path)


def test_chart_calibration_check(out_dir: Path) -> None:
    path = chart_calibration_check(MINIMAL_DATA, out_dir)
    _assert_png(path)


def test_chart_cost_per_task(out_dir: Path) -> None:
    path = chart_cost_per_task(MINIMAL_DATA, MINIMAL_COST, out_dir)
    _assert_png(path)


def test_chart_cost_by_mode(out_dir: Path) -> None:
    path = chart_cost_by_mode(MINIMAL_DATA, MINIMAL_COST, out_dir)
    _assert_png(path)


def _rendered_ax(monkeypatch, chart_fn, out_dir: Path, cost: dict | None = None):
    """Run the real chart and hand back the Axes it drew on.

    Re-deriving what the chart does here instead would assert against a copy of
    the code under test, so breaking the chart would leave these tests green.
    """
    axes = []
    real_subplots = generate_charts.plt.subplots

    def spy(*args, **kwargs):
        fig, ax = real_subplots(*args, **kwargs)
        axes.append(ax)
        return fig, ax

    monkeypatch.setattr(generate_charts.plt, "subplots", spy)
    chart_fn(MINIMAL_DATA, MINIMAL_COST if cost is None else cost, out_dir)
    (ax,) = axes
    return ax


def test_cost_by_mode_title_counts_tasks_not_cells(monkeypatch, out_dir: Path) -> None:
    """The one number in this module that ships to readers as an image.

    A wrong count in JSON can be re-read; this one is rendered into a PNG that
    goes into the paper. MINIMAL_COST is 3 tasks across 2 arms, so a title
    reading "6" is the cell count wearing the word "tasks".
    """
    ax = _rendered_ax(monkeypatch, chart_cost_by_mode, out_dir)
    assert "3 tasks matched across arms" in ax.get_title()


def test_cost_by_mode_draws_every_matched_arm(monkeypatch, out_dir: Path) -> None:
    """The bars are the arms the matching used, not a hard-coded display list.

    `cli` is a canonical arm (in lib.shared's MODE_ORDER) and draws in canonical
    position; an arm outside MODE_ORDER entirely must ALSO draw — ordered_modes
    appends unknown arms rather than dropping them, so the caption never counts
    tasks for an arm the figure omits.
    """
    cost = copy.deepcopy(MINIMAL_COST)
    assert "cli" in generate_charts.MODE_ORDER
    cost["comparison_economics"]["modes"] += ["cli", "zz_experimental"]
    for arm in ("cli", "zz_experimental"):
        cost["comparison_economics"]["by_mode"][arm] = {
            "tasks": 3, "total_cost_usd": 7.0, "avg_cost_per_task": 2.333333
        }

    ax = _rendered_ax(monkeypatch, chart_cost_by_mode, out_dir, cost=cost)
    assert [t.get_text() for t in ax.get_xticklabels()] == [
        "baseline", "cli", "hybrid", "zz_experimental"
    ]


class TestCostLookupIsKeyedOnTheCell:
    """A task runs in every arm, so task_id alone does not identify a cost row.

    Keying on task_id let each mode's row overwrite the previous one, and every
    series then plotted whichever row happened to land last — silently, with the
    same y-values under different mode colours (EnterpriseBench-jrgs).
    """

    def _scatter_ys(self, monkeypatch, out_dir: Path) -> dict[str, list[float]]:
        """Read back the costs the real chart plotted, per mode."""
        ax = _rendered_ax(monkeypatch, chart_cost_per_task, out_dir)
        return {
            series.get_label(): [float(y) for _, y in series.get_offsets()]
            for series in ax.collections
        }

    def test_arms_do_not_share_one_collapsed_cost_series(
        self, monkeypatch, out_dir: Path
    ) -> None:
        plotted = self._scatter_ys(monkeypatch, out_dir)
        assert plotted["baseline"] and plotted["hybrid"]
        assert plotted["baseline"] != plotted["hybrid"]

    def test_a_zero_cost_run_is_plotted_not_dropped(
        self, monkeypatch, out_dir: Path
    ) -> None:
        """`cost_entry.get("cost_usd") or ...` discarded a legitimate $0.00 point."""
        assert 0.0 in self._scatter_ys(monkeypatch, out_dir)["hybrid"]

    def test_stale_report_raises_instead_of_charting_zeros(self, out_dir: Path) -> None:
        stale = {"by_mode": {"baseline": {"total_cost": 12.5}}, "per_task": []}
        with pytest.raises(ValueError, match="schema_version"):
            chart_cost_by_mode(MINIMAL_DATA, stale, out_dir)
        with pytest.raises(ValueError, match="schema_version"):
            chart_cost_per_task(MINIMAL_DATA, stale, out_dir)


# ── Orchestrator tests ───────────────────────────────────────────────────────


def test_generate_all_charts(
    analysis_file: Path, cost_file: Path, tmp_path: Path
) -> None:
    out = tmp_path / "all_charts"
    generated = generate_all_charts(analysis_file, cost_file, out)
    assert len(generated) == 10
    for p in generated:
        _assert_png(p)


def test_generate_all_charts_no_cost(analysis_file: Path, tmp_path: Path) -> None:
    missing = tmp_path / "nonexistent_cost.json"
    out = tmp_path / "no_cost_charts"
    generated = generate_all_charts(analysis_file, missing, out)
    # 8 core charts, no cost charts
    assert len(generated) == 8
    for p in generated:
        _assert_png(p)
    chart_names = {p.name for p in generated}
    assert "cost_per_task.png" not in chart_names
    assert "cost_by_mode.png" not in chart_names


def test_generate_all_charts_cost_none(analysis_file: Path, tmp_path: Path) -> None:
    out = tmp_path / "none_cost"
    generated = generate_all_charts(analysis_file, None, out)
    assert len(generated) == 8


# ── Data-driven arm set (publish matrix: baseline/mcp_only/cli) ─────────────

CLI_ARM_DATA: dict = {
    "by_mode": {
        "baseline": {
            "count": 5,
            "mean": 0.60,
            "median": 0.60,
            "std": 0.10,
            "min": 0.4,
            "max": 0.8,
            "pass_rate": 0.40,
        },
        "mcp_only": {
            "count": 5,
            "mean": 0.70,
            "median": 0.70,
            "std": 0.10,
            "min": 0.5,
            "max": 0.9,
            "pass_rate": 0.60,
        },
        "cli": {
            "count": 5,
            "mean": 0.75,
            "median": 0.75,
            "std": 0.10,
            "min": 0.6,
            "max": 0.9,
            "pass_rate": 0.60,
        },
    },
    "by_suite": {},
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
            "mean_delta": 0.15,
            "median_delta": 0.15,
            "pct_improved": 0.8,
            "pct_degraded": 0.0,
            "pct_unchanged": 0.2,
            "cohens_d": 0.6,
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
            "task_id": "task-100",
            "suite": "s",
            "difficulty": "medium",
            "task_type": "t",
            "scores": {"baseline": 0.6, "mcp_only": 0.7, "cli": 0.75},
            "is_calibration": False,
        },
    ],
}


class TestDataDrivenArms:
    """bead k1nud: the reporting layer must not drop or mislabel the cli arm."""

    def test_modes_present_includes_cli(self) -> None:
        assert _modes_present(CLI_ARM_DATA) == ["baseline", "mcp_only", "cli"]

    def test_modes_present_orders_hybrid_last_when_present(self) -> None:
        data = {
            "by_mode": {
                "hybrid": {},
                "baseline": {},
                "cli": {},
                "mcp_only": {},
            }
        }
        assert _modes_present(data) == ["baseline", "mcp_only", "cli", "hybrid"]

    def test_cli_has_its_own_color_distinct_from_others(self) -> None:
        colors = {_mode_color(m) for m in MODE_ORDER}
        assert len(colors) == len(MODE_ORDER), "every canonical arm needs a distinct color"

    def test_delta_comparisons_includes_cli_not_hybrid(self) -> None:
        comparisons = _delta_comparisons(CLI_ARM_DATA["mcp_delta"])
        keys = [key for key, _, _ in comparisons]
        assert keys == ["mcp_only_vs_baseline", "cli_vs_baseline"]

    def test_chart_score_by_mode_cli_arm(self, out_dir: Path) -> None:
        path = chart_score_by_mode(CLI_ARM_DATA, out_dir)
        _assert_png(path)

    def test_chart_mcp_delta_cli_arm(self, out_dir: Path) -> None:
        path = chart_mcp_delta(CLI_ARM_DATA, out_dir)
        _assert_png(path)

    def test_chart_pass_rate_comparison_cli_arm(self, out_dir: Path) -> None:
        path = chart_pass_rate_comparison(CLI_ARM_DATA, out_dir)
        _assert_png(path)

    def test_chart_cost_by_mode_cli_arm(self, out_dir: Path) -> None:
        cost_data = copy.deepcopy(MINIMAL_COST)
        cost_data["comparison_economics"]["modes"] = [
            "baseline", "mcp_only", "cli"
        ]
        cost_data["comparison_economics"]["by_mode"] = {
            "baseline": {"tasks": 3, "total_cost_usd": 5.0,
                         "avg_cost_per_task": 1.666667},
            "mcp_only": {"tasks": 3, "total_cost_usd": 7.0,
                         "avg_cost_per_task": 2.333333},
            "cli": {"tasks": 3, "total_cost_usd": 6.0,
                    "avg_cost_per_task": 2.0},
        }
        path = chart_cost_by_mode(CLI_ARM_DATA, cost_data, out_dir)
        _assert_png(path)
