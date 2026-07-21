#!/usr/bin/env python3
"""Markdown report generator for EnterpriseBench.

Combines score_analysis.json, cost_report.json (optional),
reproducibility_report.json (optional), and chart image references
into a unified analysis report at results/analysis/report.md.
"""

from __future__ import annotations

import argparse
import json
import logging
import sys
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent))
from lib.shared import ordered_modes  # noqa: E402

logger = logging.getLogger(__name__)

ROOT = Path(__file__).resolve().parent.parent

# Modes considered "tool-armed" for MCP-coverage findings/recommendations —
# any arm other than baseline. Data-driven per-run (derived from whatever
# by_mode/mcp_delta actually contain), not a hard-coded {hybrid, mcp_only}
# set, so a cli-only publish sweep is reported correctly instead of every
# suite reading as "missing MCP".
_BASELINE_MODE = "baseline"


def _mode_label(mode: str) -> str:
    """Human-readable label for a mode, e.g. 'mcp_only' -> 'MCP-only'."""
    labels = {"mcp_only": "MCP-only", "cli": "CLI", "hybrid": "Hybrid", "baseline": "Baseline"}
    return labels.get(mode, mode.replace("_", " ").title())


def _delta_modes_present(mcp_delta: dict[str, Any]) -> list[str]:
    """Non-baseline arms with a "<mode>_vs_baseline" entry, canonically ordered."""
    modes = {
        key.removesuffix("_vs_baseline")
        for key in mcp_delta
        if key.endswith("_vs_baseline")
    }
    return ordered_modes(modes)


# ---------------------------------------------------------------------------
# Data containers
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class ReportInputs:
    """Immutable container for all report input data."""

    score_analysis: dict[str, Any]
    cost_report: dict[str, Any] | None
    reproducibility_report: dict[str, Any] | None
    charts_dir: Path | None
    available_charts: frozenset[str]


# ---------------------------------------------------------------------------
# Loaders
# ---------------------------------------------------------------------------


def _load_json(path: Path) -> dict[str, Any] | None:
    """Load a JSON file, returning None if it doesn't exist."""
    if not path.exists():
        logger.info("File not found (skipping): %s", path)
        return None
    with path.open() as f:
        data: dict[str, Any] = json.load(f)
    logger.info("Loaded %s", path)
    return data


def _discover_charts(charts_dir: Path | None) -> frozenset[str]:
    """Return frozenset of chart filenames found in the charts directory."""
    if charts_dir is None or not charts_dir.is_dir():
        return frozenset()
    return frozenset(p.name for p in charts_dir.glob("*.png"))


def load_inputs(
    analysis_path: Path,
    cost_path: Path,
    repro_path: Path,
    charts_dir: Path | None,
) -> ReportInputs:
    """Load all input files and return a ReportInputs container."""
    score_analysis = _load_json(analysis_path)
    if score_analysis is None:
        raise FileNotFoundError(
            f"Required score_analysis.json not found at {analysis_path}"
        )
    return ReportInputs(
        score_analysis=score_analysis,
        cost_report=_load_json(cost_path),
        reproducibility_report=_load_json(repro_path),
        charts_dir=charts_dir,
        available_charts=_discover_charts(charts_dir),
    )


# ---------------------------------------------------------------------------
# Table helpers
# ---------------------------------------------------------------------------


def _fmt(value: Any, decimals: int = 4) -> str:
    """Format a numeric value for display."""
    if value is None:
        return "N/A"
    if isinstance(value, float):
        return f"{value:.{decimals}f}"
    return str(value)


def _pct(value: float | None) -> str:
    """Format a float as a percentage string."""
    if value is None:
        return "N/A"
    return f"{value * 100:.1f}%"


def make_table(headers: list[str], rows: list[list[str]]) -> str:
    """Build a markdown table from headers and rows."""
    if not rows:
        return "_No data available._\n"
    lines = [
        "| " + " | ".join(headers) + " |",
        "| " + " | ".join("---" for _ in headers) + " |",
    ]
    for row in rows:
        lines.append("| " + " | ".join(row) + " |")
    return "\n".join(lines) + "\n"


def _chart_ref(filename: str, alt: str, available: frozenset[str]) -> str:
    """Return a markdown image reference if the chart exists, else empty."""
    if filename in available:
        return f"\n![{alt}](charts/{filename})\n"
    return ""


# ---------------------------------------------------------------------------
# Section builders
# ---------------------------------------------------------------------------


def _cohens_d_interpretation(d: float | None) -> str:
    """Return qualitative interpretation of Cohen's d."""
    if d is None:
        return "N/A"
    abs_d = abs(d)
    if abs_d < 0.2:
        return "negligible"
    if abs_d < 0.5:
        return "small"
    if abs_d < 0.8:
        return "medium"
    return "large"


def _count_all_modes_tasks(
    per_task: list[dict[str, Any]],
    modes: set[str] | None = None,
) -> int:
    """Count tasks that have scores for every mode in *modes*.

    *modes* defaults to the union of modes actually seen across per_task's
    scores — data-driven, so this is not pinned to a hard-coded 3-arm set and
    tracks whatever arms the loaded run artifacts contain (e.g. the publish
    matrix's baseline/mcp_only/cli, with no hybrid).
    """
    if modes is None:
        modes = {m for task in per_task for m in task.get("scores", {})}
    count = 0
    for task in per_task:
        scores = task.get("scores", {})
        if modes.issubset(scores.keys()) and all(scores[m] is not None for m in modes):
            count += 1
    return count


def build_executive_summary(inputs: ReportInputs) -> str:
    """Generate the executive summary section."""
    sa = inputs.score_analysis
    by_mode = sa.get("by_mode", {})
    total = sa.get("total_results", 0)
    modes = list(by_mode.keys())

    bullets: list[str] = []

    # Headline
    baseline = by_mode.get("baseline", {})
    baseline_pr = baseline.get("pass_rate")
    bullets.append(
        f"**{total} task results** scored across {len(modes)} mode(s). "
        f"Baseline pass rate: {_pct(baseline_pr)}."
    )

    # MCP impact direction — one bullet per non-baseline arm present in the data
    # (data-driven: previously hard-coded to "hybrid" alone, which silently said
    # nothing about a cli-only or mcp_only-only sweep).
    mcp_delta = sa.get("mcp_delta", {})
    for mode in _delta_modes_present(mcp_delta):
        delta = mcp_delta.get(f"{mode}_vs_baseline", {})
        mean_d = delta.get("mean_delta")
        n_paired = delta.get("n_paired", 0)
        if mean_d is not None:
            direction = (
                "positive" if mean_d > 0 else "negative" if mean_d < 0 else "neutral"
            )
            bullets.append(
                f"{_mode_label(mode)} vs baseline mean delta: {_fmt(mean_d)} "
                f"({direction}), based on {n_paired} paired task(s)."
            )

    # Flags
    cal = sa.get("calibration_bias", {})
    if cal.get("bias_flagged"):
        bullets.append(
            "**WARNING:** Calibration bias flagged -- investigate mode sensitivity."
        )
    else:
        bullets.append(
            "Calibration bias check: PASS (no significant mode sensitivity)."
        )

    return "\n".join(f"- {b}" for b in bullets) + "\n"


def build_coverage_section(inputs: ReportInputs) -> str:
    """Generate the coverage section."""
    sa = inputs.score_analysis
    by_mode = sa.get("by_mode", {})
    per_task = sa.get("per_task", [])
    total = sa.get("total_results", 0)
    modes = ordered_modes(by_mode.keys())
    task_modes = {m for task in per_task for m in task.get("scores", {})}
    all_modes_count = _count_all_modes_tasks(per_task, task_modes)

    lines = [
        f"- Total unique results: {total} across {len(modes)} mode(s)",
    ]
    for mode in modes:
        stats = by_mode[mode]
        lines.append(
            f"- {mode.replace('_', ' ').title()}: {stats['count']} tasks scored"
        )
    lines.append(f"- Tasks with all {len(task_modes)} modes: {all_modes_count}")
    return "\n".join(lines) + "\n"


def build_score_by_mode(inputs: ReportInputs) -> str:
    """Generate score distribution by mode table."""
    by_mode = inputs.score_analysis.get("by_mode", {})
    headers = ["Mode", "N", "Mean", "Median", "Std", "Pass Rate"]
    rows: list[list[str]] = []
    for mode in ordered_modes(by_mode.keys()):
        s = by_mode[mode]
        rows.append(
            [
                mode,
                str(s.get("count", 0)),
                _fmt(s.get("mean")),
                _fmt(s.get("median")),
                _fmt(s.get("std")),
                _pct(s.get("pass_rate")),
            ]
        )
    table = make_table(headers, rows)
    chart = _chart_ref("score_by_mode.png", "Score by Mode", inputs.available_charts)
    return table + chart


def _build_breakdown_table(
    data: dict[str, dict[str, Any]],
    label: str,
    inputs: ReportInputs,
) -> str:
    """Build a breakdown table for suite/difficulty/task_type."""
    headers = [label, "Mode", "N", "Mean", "Pass Rate"]
    rows: list[list[str]] = []
    for key in sorted(data.keys()):
        modes = data[key]
        for mode in ordered_modes(modes.keys()):
            s = modes[mode]
            rows.append(
                [
                    key,
                    mode,
                    str(s.get("count", 0)),
                    _fmt(s.get("mean")),
                    _pct(s.get("pass_rate")),
                ]
            )
    return make_table(headers, rows)


def build_score_by_suite(inputs: ReportInputs) -> str:
    """Generate score distribution by suite."""
    by_suite = inputs.score_analysis.get("by_suite", {})
    table = _build_breakdown_table(by_suite, "Suite", inputs)
    chart = _chart_ref("score_heatmap.png", "Score Heatmap", inputs.available_charts)
    return table + chart


def build_score_by_difficulty(inputs: ReportInputs) -> str:
    """Generate score distribution by difficulty."""
    by_diff = inputs.score_analysis.get("by_difficulty", {})
    table = _build_breakdown_table(by_diff, "Difficulty", inputs)
    chart = _chart_ref(
        "difficulty_distribution.png",
        "Difficulty Distribution",
        inputs.available_charts,
    )
    return table + chart


def build_score_by_task_type(inputs: ReportInputs) -> str:
    """Generate score distribution by task type."""
    by_tt = inputs.score_analysis.get("by_task_type", {})
    return _build_breakdown_table(by_tt, "Task Type", inputs)


def _build_delta_block(label: str, delta: dict[str, Any]) -> str:
    """Build a single MCP delta comparison block."""
    n = delta.get("n_paired", 0)
    mean_d = delta.get("mean_delta")
    direction = (
        "improvement"
        if (mean_d or 0) > 0
        else "regression" if (mean_d or 0) < 0 else "no change"
    )
    cohens = delta.get("cohens_d")
    wilcoxon = delta.get("wilcoxon_p")
    sig = delta.get("significant", False)
    note = delta.get("note", "")

    lines = [
        f"### {label}",
        f"- Paired tasks: {n}",
        f"- Mean delta: {_fmt(mean_d)} ({direction})",
        f"- Median delta: {_fmt(delta.get('median_delta'))}",
        f"- Tasks improved: {_pct(delta.get('pct_improved'))}, "
        f"degraded: {_pct(delta.get('pct_degraded'))}, "
        f"unchanged: {_pct(delta.get('pct_unchanged'))}",
        f"- Cohen's d: {_fmt(cohens)} ({_cohens_d_interpretation(cohens)})",
        f"- Wilcoxon p-value: {_fmt(wilcoxon)} "
        f"({'significant' if sig else 'not significant'} at alpha=0.05)",
    ]
    if n < 20:
        lines.append(
            "- **Note:** Insufficient paired data for reliable statistical conclusions. "
            "Complete the sweep to get meaningful results."
        )
    if note:
        lines.append(f"- _{note}_")
    return "\n".join(lines) + "\n"


def build_mcp_impact(inputs: ReportInputs) -> str:
    """Generate MCP impact analysis section: one block per non-baseline arm."""
    mcp_delta = inputs.score_analysis.get("mcp_delta", {})
    parts: list[str] = []

    for mode in _delta_modes_present(mcp_delta):
        delta = mcp_delta.get(f"{mode}_vs_baseline")
        if delta:
            parts.append(_build_delta_block(f"{_mode_label(mode)} vs Baseline", delta))

    if not parts:
        parts.append("_No MCP delta data available._\n")

    chart = _chart_ref("mcp_delta.png", "MCP Delta", inputs.available_charts)
    return "\n".join(parts) + chart


def build_calibration_section(inputs: ReportInputs) -> str:
    """Generate calibration bias check section."""
    cal = inputs.score_analysis.get("calibration_bias", {})
    if not cal:
        return "_No calibration bias data available._\n"

    flagged = cal.get("bias_flagged", False)
    status = "FAIL" if flagged else "PASS"
    interpretation = (
        "Calibration bias detected -- mode sensitivity exceeds threshold. "
        "Investigate potential MCP-favorable task design."
        if flagged
        else "Calibration tasks show minimal mode sensitivity, suggesting the "
        "benchmark is not biased toward MCP-equipped agents."
    )

    lines = [
        f"- Calibration tasks: {cal.get('calibration_task_count', 'N/A')}",
        f"- Max mode delta: {_fmt(cal.get('max_mode_delta'))}",
        f"- Bias threshold: {_fmt(cal.get('bias_threshold'))}",
        f"- Status: **{status}**",
        f"- {interpretation}",
    ]

    mean_by_mode = cal.get("mean_by_mode", {})
    if mean_by_mode:
        mode_strs = [f"{m}: {_fmt(v)}" for m, v in sorted(mean_by_mode.items())]
        lines.append(f"- Mean by mode: {', '.join(mode_strs)}")

    chart = _chart_ref(
        "calibration_check.png", "Calibration Check", inputs.available_charts
    )
    return "\n".join(lines) + "\n" + chart


def build_reproducibility_section(inputs: ReportInputs) -> str:
    """Generate reproducibility section."""
    rr = inputs.reproducibility_report
    if rr is None:
        return (
            "Reproducibility report not yet generated. Run "
            "`python3 scripts/reproducibility_check.py` after completing repeat runs.\n"
        )

    status = "PASS" if rr.get("pass", False) else "FAIL"
    lines = [
        f"- Tasks sampled: {rr.get('sample_size', 'N/A')}",
        f"- Tasks with multiple runs: {rr.get('tasks_with_multiple_runs', 'N/A')}",
        f"- Tasks flagged (variance >= {rr.get('variance_threshold', 0.15)}): "
        f"{rr.get('tasks_flagged', 'N/A')}",
        f"- Mean variance: {_fmt(rr.get('mean_variance'))}",
        f"- Status: **{status}**",
    ]
    return "\n".join(lines) + "\n"


def build_cost_section(inputs: ReportInputs) -> str:
    """Generate cost analysis section."""
    cr = inputs.cost_report
    if cr is None:
        return (
            "Cost report not yet generated. Run "
            "`python3 scripts/cost_tracker.py` to generate.\n"
        )

    lines = [f"- Total cost: ${cr.get('total_cost_usd', 0):.2f}"]

    by_mode = cr.get("by_mode", {})
    if by_mode:
        # Latency rides next to cost in the same table — sourced from
        # cost_tracker.py's per-arm "latency_seconds" (median/p90 wall-clock,
        # read from the "timing"."agent" field run_task.py writes), not a
        # separate report a reader has to go cross-reference.
        headers = ["Mode", "Tasks", "Total Cost", "Avg Cost", "Median Latency", "P90 Latency"]
        rows: list[list[str]] = []
        for mode in ordered_modes(by_mode.keys()):
            s = by_mode[mode]
            latency = s.get("latency_seconds", {})
            median = latency.get("median")
            p90 = latency.get("p90")
            rows.append(
                [
                    mode,
                    str(s.get("count", 0)),
                    f"${s.get('total_cost', 0):.2f}",
                    f"${s.get('avg_cost', 0):.2f}",
                    f"{median:.1f}s" if median is not None else "N/A",
                    f"{p90:.1f}s" if p90 is not None else "N/A",
                ]
            )
        lines.append(make_table(headers, rows))

    total_tasks = cr.get("total_tasks", 0)
    total_cost = cr.get("total_cost_usd", 0)
    if total_tasks > 0:
        lines.append(f"- Average cost per task: ${total_cost / total_tasks:.2f}")

    chart = _chart_ref("cost_by_mode.png", "Cost by Mode", inputs.available_charts)
    return "\n".join(lines) + "\n" + chart


def build_key_findings(inputs: ReportInputs) -> str:
    """Generate key findings as bullet points."""
    sa = inputs.score_analysis
    by_suite = sa.get("by_suite", {})
    by_mode = sa.get("by_mode", {})
    mcp_delta = sa.get("mcp_delta", {})
    findings: list[str] = []

    # Find highest/lowest scoring suites (baseline)
    suite_means: list[tuple[str, float]] = []
    for suite, modes in by_suite.items():
        bl = modes.get("baseline", {})
        if bl.get("mean") is not None:
            suite_means.append((suite, bl["mean"]))

    if suite_means:
        suite_means.sort(key=lambda x: x[1], reverse=True)
        best = suite_means[0]
        worst = suite_means[-1]
        findings.append(
            f"Highest-scoring suite (baseline): **{best[0]}** "
            f"(mean {_fmt(best[1])})."
        )
        findings.append(
            f"Lowest-scoring suite (baseline): **{worst[0]}** "
            f"(mean {_fmt(worst[1])})."
        )

    # MCP direction — one finding per non-baseline arm present in the data
    for mode in _delta_modes_present(mcp_delta):
        delta = mcp_delta.get(f"{mode}_vs_baseline", {})
        mean_d = delta.get("mean_delta")
        n_paired = delta.get("n_paired", 0)
        label = _mode_label(mode)
        if mean_d is None:
            continue
        if mean_d > 0:
            findings.append(
                f"{label} mode shows a positive mean delta of {_fmt(mean_d)} "
                f"over baseline ({n_paired} paired tasks)."
            )
        elif mean_d < 0:
            findings.append(
                f"{label} mode shows a negative mean delta of {_fmt(mean_d)} "
                f"vs baseline ({n_paired} paired tasks) -- "
                "likely due to small sample size."
            )
        else:
            findings.append(f"{label} mode shows no difference from baseline.")

    # Data gaps — any tool-armed arm (every mode but baseline) trailing baseline's count
    baseline_count = by_mode.get("baseline", {}).get("count", 0)
    mcp_modes = set(by_mode.keys()) - {_BASELINE_MODE}
    mcp_counts = {m: by_mode.get(m, {}).get("count", 0) for m in mcp_modes}
    if mcp_counts and any(c < baseline_count for c in mcp_counts.values()):
        gap = baseline_count - max(mcp_counts.values())
        findings.append(
            f"Data gap: {gap}+ tasks lack MCP mode results. "
            "Complete the sweep for full coverage."
        )

    # Suites missing MCP (any non-baseline arm actually present in the overall data)
    missing_mcp_suites: list[str] = []
    for suite, modes in by_suite.items():
        if not any(m in modes for m in mcp_modes):
            missing_mcp_suites.append(suite)
    if missing_mcp_suites:
        findings.append(
            f"Suites with no MCP results: {', '.join(sorted(missing_mcp_suites))}."
        )

    if not findings:
        findings.append("Insufficient data for key findings.")

    return "\n".join(f"- {f}" for f in findings) + "\n"


def build_recommendations(inputs: ReportInputs) -> str:
    """Generate recommendations section."""
    sa = inputs.score_analysis
    by_suite = sa.get("by_suite", {})
    by_mode = sa.get("by_mode", {})
    mcp_delta = sa.get("mcp_delta", {})
    cal = sa.get("calibration_bias", {})
    recs: list[str] = []

    # Check paired count — the weakest-paired arm actually present, data-driven
    # (previously hard-coded to "hybrid" alone).
    delta_modes = _delta_modes_present(mcp_delta)
    n_paired = min(
        (mcp_delta[f"{m}_vs_baseline"].get("n_paired", 0) for m in delta_modes),
        default=0,
    )
    if n_paired < 20:
        recs.append(
            "Complete the sweep to achieve statistical power for MCP impact analysis "
            f"(currently {n_paired} paired tasks, need 20+)."
        )

    # Suites with 0 results from any tool-armed arm actually present in the data
    mcp_modes = set(by_mode.keys()) - {_BASELINE_MODE}
    missing: list[str] = []
    for suite, modes in by_suite.items():
        if not any(m in modes for m in mcp_modes):
            missing.append(suite)
    if missing:
        recs.append(
            f"Run MCP modes for {', '.join(sorted(missing))} to complete coverage."
        )

    # Calibration bias
    if cal.get("bias_flagged"):
        recs.append("Investigate calibration task mode differences.")

    # Reproducibility
    if inputs.reproducibility_report is None:
        recs.append(
            "Execute 3x repeat runs on 25 stratified tasks for reproducibility validation."
        )

    if not recs:
        recs.append("No immediate action items -- coverage and quality look good.")

    return "\n".join(f"- {r}" for r in recs) + "\n"


# ---------------------------------------------------------------------------
# Report assembly
# ---------------------------------------------------------------------------


def generate_report(inputs: ReportInputs) -> str:
    """Assemble the full markdown report from all sections."""
    generated = inputs.score_analysis.get(
        "generated_at",
        datetime.now(timezone.utc).isoformat(),
    )

    sections = [
        "# EnterpriseBench Analysis Report",
        f"\nGenerated: {generated}\n",
        "## Executive Summary\n",
        build_executive_summary(inputs),
        "## 1. Coverage\n",
        build_coverage_section(inputs),
        "## 2. Score Distributions\n",
        "### By Mode\n",
        build_score_by_mode(inputs),
        "### By Suite\n",
        build_score_by_suite(inputs),
        "### By Difficulty\n",
        build_score_by_difficulty(inputs),
        "### By Task Type\n",
        build_score_by_task_type(inputs),
        "## 3. MCP Impact Analysis\n",
        build_mcp_impact(inputs),
        "## 4. Calibration Bias Check\n",
        build_calibration_section(inputs),
        "## 5. Reproducibility\n",
        build_reproducibility_section(inputs),
        "## 6. Cost Analysis\n",
        build_cost_section(inputs),
        "## 7. Key Findings\n",
        build_key_findings(inputs),
        "## 8. Recommendations\n",
        build_recommendations(inputs),
    ]
    return "\n".join(sections)


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    """Parse command-line arguments."""
    parser = argparse.ArgumentParser(
        description="Generate EnterpriseBench analysis report (Markdown).",
    )
    parser.add_argument(
        "--analysis",
        type=Path,
        default=ROOT / "results" / "analysis" / "score_analysis.json",
        help="Path to score_analysis.json (required)",
    )
    parser.add_argument(
        "--cost-report",
        type=Path,
        default=ROOT / "results" / "cost_report.json",
        help="Path to cost_report.json (optional)",
    )
    parser.add_argument(
        "--reproducibility-report",
        type=Path,
        default=ROOT / "results" / "reproducibility_report.json",
        help="Path to reproducibility_report.json (optional)",
    )
    parser.add_argument(
        "--charts-dir",
        type=Path,
        default=ROOT / "results" / "analysis" / "charts",
        help="Directory containing chart PNGs",
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=ROOT / "results" / "analysis" / "report.md",
        help="Output path for the report",
    )
    parser.add_argument(
        "-v",
        "--verbose",
        action="store_true",
        help="Enable verbose logging",
    )
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> None:
    """Entry point."""
    args = parse_args(argv)

    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(levelname)s: %(message)s",
    )

    inputs = load_inputs(
        analysis_path=args.analysis,
        cost_path=args.cost_report,
        repro_path=args.reproducibility_report,
        charts_dir=args.charts_dir,
    )

    report = generate_report(inputs)

    output_path: Path = args.output
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(report)
    logger.info("Report written to %s (%d bytes)", output_path, len(report))


if __name__ == "__main__":
    main()
