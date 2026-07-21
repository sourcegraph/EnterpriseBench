#!/usr/bin/env python3
"""Generate matplotlib/seaborn charts from EnterpriseBench analysis data.

Reads score_analysis.json and optionally cost_report.json, produces PNG charts
into results/analysis/charts/.

Usage:
    python3 scripts/generate_charts.py [--analysis PATH] [--cost-report PATH] [--output-dir DIR] [-v]
"""

from __future__ import annotations

import argparse
import json
import logging
import sys
from pathlib import Path
from typing import Any

import matplotlib

matplotlib.use("Agg")

import matplotlib.pyplot as plt  # noqa: E402
import numpy as np  # noqa: E402
import seaborn as sns  # noqa: E402

sys.path.insert(0, str(Path(__file__).resolve().parent))
from lib.shared import MODE_ORDER, ordered_modes as _order_present  # noqa: E402

logger = logging.getLogger(__name__)

# ── Palette ──────────────────────────────────────────────────────────────────
# MODE_ORDER (canonical arm ordering: baseline, mcp_only, cli, hybrid-if-
# present) is the single source of truth in lib.shared, shared with
# analyze_scores.py and generate_report.py — a color is chart-specific so it
# stays local, but the ordering it renders in must not drift from theirs.
MODE_COLORS: dict[str, str] = {
    "baseline": "#4C72B0",
    "mcp_only": "#55A868",
    "cli": "#8172B2",
    "hybrid": "#DD8452",
}
DPI = 150

_FALLBACK_COLOR = "#888888"


def _mode_color(mode: str) -> str:
    """Color for a mode, with a stable fallback for an arm outside MODE_COLORS."""
    return MODE_COLORS.get(mode, _FALLBACK_COLOR)


def _modes_present(data: dict[str, Any]) -> list[str]:
    """Return arms present in by_mode, ordered per MODE_ORDER (unknown arms last)."""
    return _order_present(data.get("by_mode", {}))


def _setup_style() -> None:
    sns.set_theme(style="whitegrid", palette=list(MODE_COLORS.values()))


# ── Individual chart functions ───────────────────────────────────────────────


def chart_score_by_mode(data: dict[str, Any], out: Path) -> Path:
    """Grouped bar chart: mean normalized score per mode with error bars."""
    by_mode = data["by_mode"]
    modes = _modes_present(data)
    means = [by_mode[m]["mean"] for m in modes]
    stds = [by_mode[m]["std"] for m in modes]
    counts = [by_mode[m]["count"] for m in modes]
    colors = [_mode_color(m) for m in modes]

    fig, ax = plt.subplots(figsize=(10, 6))
    bars = ax.bar(modes, means, yerr=stds, color=colors, capsize=5, edgecolor="white")
    for bar, cnt in zip(bars, counts):
        ax.text(
            bar.get_x() + bar.get_width() / 2,
            bar.get_height() + 0.02,
            f"n={cnt}",
            ha="center",
            va="bottom",
            fontsize=10,
        )
    ax.set_ylim(0, 1.15)
    ax.set_ylabel("Mean Normalized Score")
    ax.set_title("Score by Mode")
    ax.legend(bars, modes, title="Mode")
    dest = out / "score_by_mode.png"
    fig.tight_layout()
    fig.savefig(dest, dpi=DPI)
    plt.close(fig)
    logger.info("Saved %s", dest)
    return dest


def chart_score_heatmap(data: dict[str, Any], out: Path) -> Path:
    """Heatmap: rows=suites, columns=modes."""
    by_suite = data.get("by_suite", {})
    modes = _modes_present(data)
    suites = sorted(by_suite.keys())

    matrix: list[list[float | None]] = []
    annots: list[list[str]] = []
    for suite in suites:
        row_vals: list[float | None] = []
        row_annot: list[str] = []
        for mode in modes:
            entry = by_suite[suite].get(mode)
            if entry:
                row_vals.append(entry["mean"])
                row_annot.append(f"{entry['mean']:.2f}\nn={entry['count']}")
            else:
                row_vals.append(None)
                row_annot.append("")
        matrix.append(row_vals)
        annots.append(row_annot)

    arr = np.array(matrix, dtype=float)
    annot_arr = np.array(annots)

    fig, ax = plt.subplots(figsize=(12, 8))
    sns.heatmap(
        arr,
        xticklabels=modes,
        yticklabels=suites,
        annot=annot_arr,
        fmt="",
        cmap="RdYlGn",
        vmin=0,
        vmax=1,
        linewidths=0.5,
        ax=ax,
    )
    ax.set_title("Score Heatmap by Suite and Mode")
    dest = out / "score_heatmap.png"
    fig.tight_layout()
    fig.savefig(dest, dpi=DPI)
    plt.close(fig)
    logger.info("Saved %s", dest)
    return dest


def chart_difficulty_distribution(data: dict[str, Any], out: Path) -> Path:
    """Grouped bar chart: mean score by difficulty level per mode."""
    by_diff = data.get("by_difficulty", {})
    modes = _modes_present(data)
    diff_order = [d for d in ["easy", "medium", "hard", "expert"] if d in by_diff]

    x = np.arange(len(diff_order))
    width = 0.8 / max(len(modes), 1)

    fig, ax = plt.subplots(figsize=(10, 6))
    for i, mode in enumerate(modes):
        means = []
        stds = []
        for d in diff_order:
            entry = by_diff[d].get(mode)
            means.append(entry["mean"] if entry else 0)
            stds.append(entry["std"] if entry else 0)
        ax.bar(
            x + i * width,
            means,
            width,
            yerr=stds,
            label=mode,
            color=_mode_color(mode),
            capsize=3,
        )

    ax.set_xticks(x + width * (len(modes) - 1) / 2)
    ax.set_xticklabels(diff_order)
    ax.set_ylim(0, 1.15)
    ax.set_ylabel("Mean Normalized Score")
    ax.set_title("Score by Difficulty Level")
    ax.legend(title="Mode")
    dest = out / "difficulty_distribution.png"
    fig.tight_layout()
    fig.savefig(dest, dpi=DPI)
    plt.close(fig)
    logger.info("Saved %s", dest)
    return dest


def chart_task_type_scores(data: dict[str, Any], out: Path) -> Path:
    """Horizontal bar chart: mean score per task_type, colored by mode."""
    by_tt = data.get("by_task_type", {})
    modes = _modes_present(data)
    task_types = sorted(by_tt.keys())

    y = np.arange(len(task_types))
    height = 0.8 / max(len(modes), 1)

    fig, ax = plt.subplots(figsize=(10, 6))
    for i, mode in enumerate(modes):
        vals = []
        for tt in task_types:
            entry = by_tt[tt].get(mode)
            vals.append(entry["mean"] if entry else 0)
        ax.barh(
            y + i * height,
            vals,
            height,
            label=mode,
            color=_mode_color(mode),
        )

    ax.set_yticks(y + height * (len(modes) - 1) / 2)
    ax.set_yticklabels(task_types)
    ax.set_xlim(0, 1.1)
    ax.set_xlabel("Mean Normalized Score")
    ax.set_title("Score by Task Type")
    ax.legend(title="Mode")
    dest = out / "task_type_scores.png"
    fig.tight_layout()
    fig.savefig(dest, dpi=DPI)
    plt.close(fig)
    logger.info("Saved %s", dest)
    return dest


def _delta_comparisons(mcp_delta: dict[str, Any]) -> list[tuple[str, str, str]]:
    """(key, label, color) for every "<mode>_vs_baseline" entry mcp_delta carries.

    Data-driven from the analysis output rather than a hard-coded
    {hybrid, mcp_only} pair — an arm like "cli" (or any future arm) shows up
    here automatically instead of being dropped from the chart, ordered per
    MODE_ORDER.
    """
    modes = {
        key.removesuffix("_vs_baseline")
        for key in mcp_delta
        if key.endswith("_vs_baseline")
    }
    return [
        (f"{mode}_vs_baseline", f"{mode} - baseline", _mode_color(mode))
        for mode in _order_present(modes)
    ]


def chart_mcp_delta(data: dict[str, Any], out: Path) -> Path:
    """Bar chart of mean deltas with scatter overlay of per-task deltas."""
    mcp_delta = data.get("mcp_delta", {})
    per_task = data.get("per_task", [])

    labels: list[str] = []
    means: list[float] = []
    colors_list: list[str] = []
    comparisons = _delta_comparisons(mcp_delta)
    for key, label, color in comparisons:
        if key in mcp_delta:
            labels.append(label)
            means.append(mcp_delta[key]["mean_delta"])
            colors_list.append(color)

    if not labels:
        logger.warning("No MCP delta data; skipping mcp_delta chart")
        return out / "mcp_delta.png"

    fig, ax = plt.subplots(figsize=(10, 6))
    ax.bar(labels, means, color=colors_list, edgecolor="white", zorder=2)
    ax.axhline(y=0, color="black", linewidth=0.8, linestyle="--", zorder=1)

    # Scatter individual task deltas
    for key, label, color in comparisons:
        if key not in mcp_delta:
            continue
        alt_mode = key.split("_vs_")[0]
        deltas = []
        for t in per_task:
            scores = t.get("scores", {})
            baseline_score = scores.get("baseline")
            alt_score = scores.get(alt_mode)
            if baseline_score is not None and alt_score is not None:
                deltas.append(alt_score - baseline_score)
        if deltas:
            jitter = np.random.default_rng(42).uniform(-0.15, 0.15, len(deltas))
            x_idx = labels.index(label)
            ax.scatter(
                [x_idx + j for j in jitter],
                deltas,
                color=color,
                alpha=0.6,
                edgecolor="white",
                s=40,
                zorder=3,
            )

    ax.set_ylabel("Score Delta")
    ax.set_title("MCP Delta (vs Baseline)")
    dest = out / "mcp_delta.png"
    fig.tight_layout()
    fig.savefig(dest, dpi=DPI)
    plt.close(fig)
    logger.info("Saved %s", dest)
    return dest


def chart_pass_rate_comparison(data: dict[str, Any], out: Path) -> Path:
    """Bar chart: pass rate per mode."""
    by_mode = data["by_mode"]
    modes = _modes_present(data)
    rates = [by_mode[m]["pass_rate"] for m in modes]
    colors = [_mode_color(m) for m in modes]

    fig, ax = plt.subplots(figsize=(10, 6))
    bars = ax.bar(modes, rates, color=colors, edgecolor="white")
    for bar, rate in zip(bars, rates):
        ax.text(
            bar.get_x() + bar.get_width() / 2,
            bar.get_height() + 0.01,
            f"{rate:.1%}",
            ha="center",
            va="bottom",
            fontsize=10,
        )
    ax.set_ylim(0, 1.15)
    ax.set_ylabel("Pass Rate (all_passed)")
    ax.set_title("Pass Rate by Mode")
    dest = out / "pass_rate_comparison.png"
    fig.tight_layout()
    fig.savefig(dest, dpi=DPI)
    plt.close(fig)
    logger.info("Saved %s", dest)
    return dest


def chart_per_task_scores(data: dict[str, Any], out: Path) -> Path:
    """Cleveland dot plot: each task on y-axis, score on x-axis."""
    per_task = data.get("per_task", [])
    if not per_task:
        logger.warning("No per_task data; skipping per_task_scores chart")
        return out / "per_task_scores.png"

    # Sort by baseline score (fallback to 0)
    tasks = sorted(per_task, key=lambda t: t.get("scores", {}).get("baseline", 0))
    modes = _modes_present(data)

    fig_height = max(8, len(tasks) * 0.22)
    fig, ax = plt.subplots(figsize=(12, fig_height))

    y_labels = [t["task_id"] for t in tasks]
    y_pos = np.arange(len(tasks))

    for mode in modes:
        xs = []
        ys = []
        for i, t in enumerate(tasks):
            score = t.get("scores", {}).get(mode)
            if score is not None:
                xs.append(score)
                ys.append(i)
        ax.scatter(xs, ys, label=mode, color=_mode_color(mode), s=25, zorder=3)

    ax.set_yticks(y_pos)
    ax.set_yticklabels(y_labels, fontsize=6)
    ax.set_xlim(-0.05, 1.1)
    ax.set_xlabel("Normalized Score")
    ax.set_title("Per-Task Scores")
    ax.legend(title="Mode", loc="lower right")
    dest = out / "per_task_scores.png"
    fig.tight_layout()
    fig.savefig(dest, dpi=DPI)
    plt.close(fig)
    logger.info("Saved %s", dest)
    return dest


def chart_calibration_check(data: dict[str, Any], out: Path) -> Path:
    """Compare calibration task scores across modes vs non-calibration."""
    per_task = data.get("per_task", [])
    modes = _modes_present(data)

    cal_scores: dict[str, list[float]] = {m: [] for m in modes}
    non_cal_scores: dict[str, list[float]] = {m: [] for m in modes}

    for t in per_task:
        is_cal = t.get("is_calibration", False)
        dest_dict = cal_scores if is_cal else non_cal_scores
        for mode in modes:
            score = t.get("scores", {}).get(mode)
            if score is not None:
                dest_dict[mode].append(score)

    categories = ["Calibration", "Non-Calibration"]
    x = np.arange(len(categories))
    width = 0.8 / max(len(modes), 1)

    fig, ax = plt.subplots(figsize=(10, 6))
    for i, mode in enumerate(modes):
        cal_mean = float(np.mean(cal_scores[mode])) if cal_scores[mode] else 0
        non_cal_mean = (
            float(np.mean(non_cal_scores[mode])) if non_cal_scores[mode] else 0
        )
        ax.bar(
            x + i * width,
            [cal_mean, non_cal_mean],
            width,
            label=mode,
            color=_mode_color(mode),
        )

    ax.set_xticks(x + width * (len(modes) - 1) / 2)
    ax.set_xticklabels(categories)
    ax.set_ylim(0, 1.15)
    ax.set_ylabel("Mean Normalized Score")
    ax.set_title("Calibration Check: Mode Independence")
    ax.legend(title="Mode")
    dest = out / "calibration_check.png"
    fig.tight_layout()
    fig.savefig(dest, dpi=DPI)
    plt.close(fig)
    logger.info("Saved %s", dest)
    return dest


def chart_cost_per_task(
    data: dict[str, Any], cost_data: dict[str, Any], out: Path
) -> Path:
    """Scatter plot: x=score, y=cost_usd, colored by mode."""
    per_task = data.get("per_task", [])
    cost_by_task: dict[str, dict[str, Any]] = {}
    for entry in cost_data.get("per_task", cost_data.get("tasks", [])):
        tid = entry.get("task_id", "")
        cost_by_task[tid] = entry

    modes = _modes_present(data)
    fig, ax = plt.subplots(figsize=(10, 6))

    for mode in modes:
        xs, ys = [], []
        for t in per_task:
            score = t.get("scores", {}).get(mode)
            cost_entry = cost_by_task.get(t["task_id"], {})
            cost = cost_entry.get("cost_usd") or cost_entry.get("costs", {}).get(mode)
            if score is not None and cost is not None:
                xs.append(score)
                ys.append(cost)
        if xs:
            ax.scatter(xs, ys, label=mode, color=_mode_color(mode), alpha=0.7, s=50)

    ax.set_xlabel("Normalized Score")
    ax.set_ylabel("Cost (USD)")
    ax.set_title("Cost vs Score per Task")
    ax.legend(title="Mode")
    dest = out / "cost_per_task.png"
    fig.tight_layout()
    fig.savefig(dest, dpi=DPI)
    plt.close(fig)
    logger.info("Saved %s", dest)
    return dest


def chart_cost_by_mode(
    data: dict[str, Any], cost_data: dict[str, Any], out: Path
) -> Path:
    """Bar chart: total cost per mode."""
    by_mode_cost = cost_data.get("by_mode", {})
    modes = _order_present(by_mode_cost)
    totals = [
        by_mode_cost[m].get("total_cost", by_mode_cost[m].get("total_cost_usd", 0))
        for m in modes
    ]
    colors = [_mode_color(m) for m in modes]

    fig, ax = plt.subplots(figsize=(10, 6))
    bars = ax.bar(modes, totals, color=colors, edgecolor="white")
    for bar, total in zip(bars, totals):
        ax.text(
            bar.get_x() + bar.get_width() / 2,
            bar.get_height() + 0.01,
            f"${total:.2f}",
            ha="center",
            va="bottom",
            fontsize=10,
        )
    ax.set_ylabel("Total Cost (USD)")
    ax.set_title("Total Cost by Mode")
    dest = out / "cost_by_mode.png"
    fig.tight_layout()
    fig.savefig(dest, dpi=DPI)
    plt.close(fig)
    logger.info("Saved %s", dest)
    return dest


# ── Orchestrator ─────────────────────────────────────────────────────────────


def generate_all_charts(
    analysis_path: Path,
    cost_report_path: Path | None,
    output_dir: Path,
) -> list[Path]:
    """Load data and generate all charts. Returns list of generated file paths."""
    _setup_style()

    with open(analysis_path) as f:
        data: dict[str, Any] = json.load(f)

    cost_data: dict[str, Any] | None = None
    if cost_report_path and cost_report_path.exists():
        with open(cost_report_path) as f:
            cost_data = json.load(f)
        logger.info("Loaded cost report from %s", cost_report_path)
    else:
        logger.info("No cost report found; skipping cost charts")

    output_dir.mkdir(parents=True, exist_ok=True)

    generated: list[Path] = []

    # Core charts (always generated)
    chart_fns = [
        chart_score_by_mode,
        chart_score_heatmap,
        chart_difficulty_distribution,
        chart_task_type_scores,
        chart_mcp_delta,
        chart_pass_rate_comparison,
        chart_per_task_scores,
        chart_calibration_check,
    ]
    for fn in chart_fns:
        try:
            path = fn(data, output_dir)
            if path.exists():
                generated.append(path)
        except Exception:
            logger.exception("Failed to generate chart: %s", fn.__name__)

    # Cost charts (only if cost data present)
    if cost_data is not None:
        cost_fns = [
            (chart_cost_per_task, (data, cost_data, output_dir)),
            (chart_cost_by_mode, (data, cost_data, output_dir)),
        ]
        for fn, args in cost_fns:
            try:
                path = fn(*args)
                if path.exists():
                    generated.append(path)
            except Exception:
                logger.exception("Failed to generate chart: %s", fn.__name__)

    return generated


# ── CLI ──────────────────────────────────────────────────────────────────────


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Generate EnterpriseBench analysis charts"
    )
    parser.add_argument(
        "--analysis",
        type=Path,
        default=Path("results/analysis/score_analysis.json"),
        help="Path to score_analysis.json",
    )
    parser.add_argument(
        "--cost-report",
        type=Path,
        default=Path("results/cost_report.json"),
        help="Path to cost_report.json (skip if missing)",
    )
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=Path("results/analysis/charts"),
        help="Output directory for charts",
    )
    parser.add_argument(
        "-v", "--verbose", action="store_true", help="Enable verbose logging"
    )
    args = parser.parse_args()

    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(levelname)s: %(message)s",
    )

    if not args.analysis.exists():
        logger.error("Analysis file not found: %s", args.analysis)
        sys.exit(1)

    generated = generate_all_charts(args.analysis, args.cost_report, args.output_dir)
    logger.info("Generated %d charts in %s", len(generated), args.output_dir)
    for p in generated:
        logger.info("  %s", p.name)


if __name__ == "__main__":
    main()
