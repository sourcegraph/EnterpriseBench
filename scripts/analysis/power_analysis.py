#!/usr/bin/env python3
"""Power analysis for mode discrimination significance.

Determines the required number of tasks (sample size) for detecting
meaningful score differences between tool-access modes (baseline vs hybrid)
at alpha=0.1, power=0.8, using pilot data to estimate effect size and
variance.

The pilot ran n=4 tasks x 3 modes x 3 reps = 36 scored runs.  Each task
yields a mean score per mode; the unit of analysis for the power calculation
is the task-level mean difference (hybrid - baseline).

Statistical approach:
  - Primary test: paired t-test on task-level mean scores (each task
    measured under both conditions)
  - Secondary: Wilcoxon signed-rank test (non-parametric; power estimated
    via simulation since no closed-form exists)
  - Effect size: Cohen's d_z (paired, using SD of within-task differences)

Usage:
    python scripts/analysis/power_analysis.py
    python scripts/analysis/power_analysis.py --pilot-csv results/phase1_pilot/summary_scored.csv
    python scripts/analysis/power_analysis.py --alpha 0.05 --power 0.9 --json
"""

from __future__ import annotations

import argparse
import csv
import json
import math
import sys
from collections import defaultdict
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

import numpy as np
from scipy import stats

PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent

# ---------------------------------------------------------------------------
# Data types
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class PilotSummary:
    """Summary statistics extracted from pilot data."""

    n_tasks: int
    n_reps_per_cell: int
    task_ids: tuple[str, ...]
    baseline_means: tuple[float, ...]
    hybrid_means: tuple[float, ...]
    differences: tuple[float, ...]
    mean_difference: float
    sd_difference: float
    cohens_dz: float | None
    per_task_cohens_d: tuple[tuple[str, float | None], ...]


@dataclass(frozen=True)
class PowerResult:
    """Result of a power analysis computation."""

    test: str
    alpha: float
    target_power: float
    effect_size_dz: float | None
    required_n: int | None
    achievable_power_at_112: float | None
    achievable_power_at_n_tasks: float | None
    note: str


@dataclass(frozen=True)
class StratifiedCell:
    """One cell in the stratified sampling plan."""

    suite: str
    stratum: str
    available: int
    allocated: int


@dataclass(frozen=True)
class StratifiedPlan:
    """Stratified sampling plan across suites and difficulty strata."""

    required_n: int
    available_n: int
    cells: tuple[StratifiedCell, ...]
    total_allocated: int
    coverage_note: str


@dataclass(frozen=True)
class FullAnalysis:
    """Complete power analysis output."""

    pilot: PilotSummary
    paired_ttest: PowerResult
    wilcoxon: PowerResult
    stratified_plan: StratifiedPlan
    recommendation: str


# ---------------------------------------------------------------------------
# Pilot data loading
# ---------------------------------------------------------------------------


def load_pilot_csv(csv_path: Path) -> dict[str, dict[str, list[float]]]:
    """Load pilot CSV into task_id -> mode -> [scores] mapping."""
    results: dict[str, dict[str, list[float]]] = defaultdict(
        lambda: defaultdict(list)
    )
    with open(csv_path, newline="") as f:
        reader = csv.DictReader(f)
        for row in reader:
            score_raw = row.get("score", "").strip()
            if not score_raw:
                continue
            try:
                score = float(score_raw)
            except (ValueError, TypeError):
                continue
            results[row["task_id"]][row["mode"]].append(score)
    return dict(results)


def summarize_pilot(
    data: dict[str, dict[str, list[float]]],
) -> PilotSummary:
    """Compute summary statistics from pilot data.

    For each task, computes the mean score under baseline and hybrid,
    then derives the paired difference (hybrid - baseline).  Cohen's d_z
    is the mean difference divided by the SD of differences.
    """
    task_ids = sorted(data.keys())
    baseline_means: list[float] = []
    hybrid_means: list[float] = []
    per_task_d: list[tuple[str, float | None]] = []

    for tid in task_ids:
        b_scores = data[tid].get("baseline", [])
        h_scores = data[tid].get("hybrid", [])
        b_mean = np.mean(b_scores) if b_scores else 0.0
        h_mean = np.mean(h_scores) if h_scores else 0.0
        baseline_means.append(float(b_mean))
        hybrid_means.append(float(h_mean))

        # Per-task independent Cohen's d (for reference)
        d = _cohens_d_independent(b_scores, h_scores)
        per_task_d.append((tid, d))

    diffs = [h - b for h, b in zip(hybrid_means, baseline_means)]
    mean_diff = float(np.mean(diffs))
    sd_diff = float(np.std(diffs, ddof=1)) if len(diffs) > 1 else 0.0

    dz = mean_diff / sd_diff if sd_diff > 0 else None
    n_reps = min(
        len(data[tid].get("baseline", [])) for tid in task_ids
    ) if task_ids else 0

    return PilotSummary(
        n_tasks=len(task_ids),
        n_reps_per_cell=n_reps,
        task_ids=tuple(task_ids),
        baseline_means=tuple(baseline_means),
        hybrid_means=tuple(hybrid_means),
        differences=tuple(diffs),
        mean_difference=mean_diff,
        sd_difference=sd_diff,
        cohens_dz=dz,
        per_task_cohens_d=tuple(per_task_d),
    )


def _cohens_d_independent(
    group1: list[float], group2: list[float]
) -> float | None:
    """Cohen's d for independent samples (reused from mode_discrimination_gate)."""
    n1, n2 = len(group1), len(group2)
    if n1 < 2 or n2 < 2:
        return None
    mean1, mean2 = np.mean(group1), np.mean(group2)
    var1 = np.var(group1, ddof=1)
    var2 = np.var(group2, ddof=1)
    pooled_var = ((n1 - 1) * var1 + (n2 - 1) * var2) / (n1 + n2 - 2)
    pooled_sd = math.sqrt(pooled_var)
    if pooled_sd == 0.0:
        return None
    return float((mean2 - mean1) / pooled_sd)


# ---------------------------------------------------------------------------
# Power analysis: paired t-test
# ---------------------------------------------------------------------------


def power_paired_ttest(
    dz: float,
    alpha: float = 0.1,
    target_power: float = 0.8,
    max_n: int = 500,
) -> tuple[int | None, float | None]:
    """Find required n for a one-sided paired t-test.

    Uses the non-central t distribution.  The test is one-sided because
    the hypothesis is directional: hybrid > baseline (positive d_z).

    Returns (required_n, achievable_power_at_112).
    """
    if dz <= 0:
        # Negative or zero effect — can't detect a positive difference
        power_112 = _paired_ttest_power(abs(dz), 112, alpha)
        return None, power_112

    required_n = None
    for n in range(2, max_n + 1):
        power = _paired_ttest_power(dz, n, alpha)
        if power >= target_power:
            required_n = n
            break

    power_112 = _paired_ttest_power(dz, 112, alpha)
    return required_n, power_112


def _paired_ttest_power(
    dz: float, n: int, alpha: float
) -> float:
    """Compute power of a one-sided paired t-test.

    Under H1, the test statistic follows a non-central t distribution
    with df = n-1 and non-centrality parameter = dz * sqrt(n).
    """
    df = n - 1
    t_crit = stats.t.ppf(1 - alpha, df)
    ncp = dz * math.sqrt(n)
    power = 1 - stats.nct.cdf(t_crit, df, ncp)
    return float(power)


# ---------------------------------------------------------------------------
# Power analysis: Wilcoxon signed-rank (simulation-based)
# ---------------------------------------------------------------------------


def power_wilcoxon_simulation(
    mean_diff: float,
    sd_diff: float,
    alpha: float = 0.1,
    target_power: float = 0.8,
    n_simulations: int = 10_000,
    max_n: int = 500,
    rng_seed: int = 42,
) -> tuple[int | None, float | None]:
    """Estimate required n for Wilcoxon signed-rank test via simulation.

    Simulates paired differences from N(mean_diff, sd_diff^2) and counts
    the fraction of simulations where the one-sided Wilcoxon test rejects.

    Returns (required_n, achievable_power_at_112).
    """
    rng = np.random.default_rng(rng_seed)

    def _sim_power(n: int) -> float:
        rejections = 0
        for _ in range(n_simulations):
            diffs = rng.normal(mean_diff, sd_diff, size=n)
            # One-sided: alternative='greater' tests if median > 0
            try:
                result = stats.wilcoxon(
                    diffs, alternative="greater", method="approx"
                )
                if result.pvalue < alpha:
                    rejections += 1
            except ValueError:
                # All differences zero or n too small
                pass
        return rejections / n_simulations

    required_n = None
    # Wilcoxon needs n >= 6 for meaningful results
    for n in range(6, max_n + 1):
        power = _sim_power(n)
        if power >= target_power:
            required_n = n
            break

    power_112 = _sim_power(112)
    return required_n, power_112


# ---------------------------------------------------------------------------
# Stratified sampling plan
# ---------------------------------------------------------------------------

# Task population: suite x difficulty_stratum counts (from benchmarks/)
TASK_POPULATION: dict[tuple[str, str], int] = {
    ("customer_escalation", "calibration"): 9,
    ("customer_escalation", "dual_repo"): 10,
    ("customer_escalation", "large_single"): 5,
    ("dependency_management", "dual_repo"): 5,
    ("dependency_management", "large_single"): 3,
    ("dependency_management", "multi_repo"): 13,
    ("dependency_management", "tri_repo"): 6,
    ("feature_delivery", "dual_repo"): 2,
    ("feature_delivery", "large_single"): 5,
    ("feature_delivery", "monorepo_cross_package"): 10,
    ("feature_delivery", "tri_repo"): 1,
    ("incident_response", "calibration"): 1,
    ("incident_response", "dual_repo"): 6,
    ("incident_response", "large_single"): 4,
    ("incident_response", "multi_repo"): 1,
    ("incident_response", "tri_repo"): 1,
    ("platform_engineering", "calibration"): 2,
    ("platform_engineering", "dual_repo"): 4,
    ("platform_engineering", "large_single"): 3,
    ("platform_engineering", "tri_repo"): 1,
    ("security_operations", "large_single"): 2,
    ("technical_debt", "calibration"): 2,
    ("technical_debt", "dual_repo"): 5,
    ("technical_debt", "large_single"): 3,
    ("technical_debt", "monorepo_cross_package"): 2,
    ("technical_debt", "tri_repo"): 7,
}

TOTAL_TASKS = sum(TASK_POPULATION.values())  # 113


def build_stratified_plan(required_n: int) -> StratifiedPlan:
    """Build a proportional stratified sampling plan.

    Allocates tasks proportionally to cell sizes, with a minimum of 1 per
    non-empty cell.  If required_n exceeds TOTAL_TASKS, uses all tasks
    and notes the shortfall.
    """
    effective_n = min(required_n, TOTAL_TASKS)

    # Proportional allocation with floor of 1
    cells: list[StratifiedCell] = []
    raw_allocations: list[tuple[tuple[str, str], float]] = []
    for key, count in sorted(TASK_POPULATION.items()):
        proportion = count / TOTAL_TASKS
        raw_alloc = proportion * effective_n
        raw_allocations.append((key, raw_alloc))

    # Allocate: floor first, distribute remainders by largest fractional part
    allocated: dict[tuple[str, str], int] = {}
    for key, raw in raw_allocations:
        allocated[key] = max(1, int(raw))

    total_alloc = sum(allocated.values())
    remainder = effective_n - total_alloc

    if remainder > 0:
        # Sort by fractional part descending, give +1 to top remainder cells
        fractional = [
            (key, raw - int(raw))
            for key, raw in raw_allocations
            if allocated[key] == int(raw)  # didn't get bumped to 1 already
        ]
        fractional.sort(key=lambda x: x[1], reverse=True)
        for key, _ in fractional[:remainder]:
            allocated[key] += 1

    for key in sorted(TASK_POPULATION.keys()):
        suite, stratum = key
        alloc = min(allocated[key], TASK_POPULATION[key])
        cells.append(
            StratifiedCell(
                suite=suite,
                stratum=stratum,
                available=TASK_POPULATION[key],
                allocated=alloc,
            )
        )

    total_allocated = sum(c.allocated for c in cells)

    if required_n > TOTAL_TASKS:
        note = (
            f"Required n={required_n} exceeds available tasks ({TOTAL_TASKS}). "
            f"Using all {TOTAL_TASKS} tasks. Consider adding tasks or "
            f"increasing reps per task to compensate."
        )
    elif required_n <= TOTAL_TASKS:
        note = (
            f"Allocating {total_allocated} of {TOTAL_TASKS} available tasks "
            f"via proportional stratification."
        )
    else:
        note = ""

    return StratifiedPlan(
        required_n=required_n,
        available_n=TOTAL_TASKS,
        cells=tuple(cells),
        total_allocated=total_allocated,
        coverage_note=note,
    )


# ---------------------------------------------------------------------------
# Full analysis
# ---------------------------------------------------------------------------


def run_analysis(
    pilot_csv: Path,
    alpha: float = 0.1,
    target_power: float = 0.8,
    n_simulations: int = 10_000,
) -> FullAnalysis:
    """Run the complete power analysis pipeline."""
    data = load_pilot_csv(pilot_csv)
    pilot = summarize_pilot(data)

    # --- Paired t-test power ---
    if pilot.cohens_dz is not None and pilot.cohens_dz > 0:
        req_n_t, pow_112_t = power_paired_ttest(
            pilot.cohens_dz, alpha, target_power
        )
        note_t = ""
    elif pilot.cohens_dz is not None and pilot.cohens_dz <= 0:
        req_n_t = None
        _, pow_112_t = power_paired_ttest(
            abs(pilot.cohens_dz), alpha, target_power
        )
        note_t = (
            f"Observed effect is negative (d_z={pilot.cohens_dz:.3f}): "
            f"hybrid underperforms baseline on average across pilot tasks. "
            f"Power computed for |d_z| to show achievable power if effect "
            f"reverses with better task design."
        )
    else:
        req_n_t = None
        pow_112_t = None
        note_t = "Cannot compute: zero variance in differences."

    ttest_result = PowerResult(
        test="paired_t_test (one-sided)",
        alpha=alpha,
        target_power=target_power,
        effect_size_dz=pilot.cohens_dz,
        required_n=req_n_t,
        achievable_power_at_112=pow_112_t,
        achievable_power_at_n_tasks=_paired_ttest_power(
            abs(pilot.cohens_dz), TOTAL_TASKS, alpha
        ) if pilot.cohens_dz is not None else None,
        note=note_t,
    )

    # --- Wilcoxon signed-rank power (simulation) ---
    if pilot.sd_difference > 0:
        req_n_w, pow_112_w = power_wilcoxon_simulation(
            abs(pilot.mean_difference),
            pilot.sd_difference,
            alpha,
            target_power,
            n_simulations,
        )
        note_w = (
            f"Simulated with {n_simulations} iterations, "
            f"N(|mean_diff|={abs(pilot.mean_difference):.4f}, "
            f"sd={pilot.sd_difference:.4f})."
        )
        if pilot.mean_difference < 0:
            note_w += (
                " Note: observed mean difference is negative; using |mean_diff| "
                "to estimate power for the positive-direction hypothesis."
            )
    else:
        req_n_w = None
        pow_112_w = None
        note_w = "Cannot compute: zero variance in differences."

    wilcoxon_result = PowerResult(
        test="wilcoxon_signed_rank (one-sided, simulation)",
        alpha=alpha,
        target_power=target_power,
        effect_size_dz=pilot.cohens_dz,
        required_n=req_n_w,
        achievable_power_at_112=pow_112_w,
        achievable_power_at_n_tasks=None,
        note=note_w,
    )

    # --- Stratified sampling plan ---
    # Use the larger of the two required n values
    effective_req = max(
        req_n_t or TOTAL_TASKS,
        req_n_w or TOTAL_TASKS,
    )
    plan = build_stratified_plan(effective_req)

    # --- Recommendation ---
    recommendation = _build_recommendation(
        pilot, ttest_result, wilcoxon_result, plan
    )

    return FullAnalysis(
        pilot=pilot,
        paired_ttest=ttest_result,
        wilcoxon=wilcoxon_result,
        stratified_plan=plan,
        recommendation=recommendation,
    )


def _build_recommendation(
    pilot: PilotSummary,
    ttest: PowerResult,
    wilcoxon: PowerResult,
    plan: StratifiedPlan,
) -> str:
    """Generate a plain-language recommendation."""
    lines: list[str] = []

    if pilot.mean_difference < 0:
        lines.append(
            "CAUTION: The pilot shows a negative mean effect (baseline > hybrid). "
            "This suggests task design issues (see phase1_pilot_analysis.md), "
            "not that MCP tools are harmful. The power analysis below assumes "
            "the effect will be positive with redesigned tasks."
        )
        lines.append("")

    req = max(ttest.required_n or 0, wilcoxon.required_n or 0)
    if req == 0:
        lines.append(
            f"Required sample size could not be determined (effect too small "
            f"or negative). Running all {TOTAL_TASKS} available tasks is "
            f"recommended."
        )
    elif req <= TOTAL_TASKS:
        lines.append(
            f"Required sample size: n={req} tasks (from the more conservative "
            f"test). This is within the {TOTAL_TASKS} available tasks."
        )
    else:
        lines.append(
            f"Required sample size: n={req} tasks, exceeding the "
            f"{TOTAL_TASKS} available. Options: (1) add more tasks, "
            f"(2) increase reps per task (currently {pilot.n_reps_per_cell}), "
            f"(3) accept lower power."
        )

    if ttest.achievable_power_at_112 is not None:
        lines.append(
            f"Power at n={TOTAL_TASKS} (all tasks): "
            f"{ttest.achievable_power_at_112:.3f} (paired t-test)."
        )

    lines.append("")
    lines.append(
        "Design recommendations to increase effect size: "
        "(1) Ensure all tasks require genuine cross-repo investigation "
        "(per Phase 1 guidelines). "
        "(2) Weight cross-repo checkpoints at 40%+ of total score. "
        "(3) Remove or redesign tasks where baseline outperforms MCP."
    )

    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Output formatting
# ---------------------------------------------------------------------------


def format_human(analysis: FullAnalysis) -> str:
    """Format analysis as human-readable text."""
    lines: list[str] = []
    p = analysis.pilot

    lines.append("=" * 72)
    lines.append("Power Analysis for Mode Discrimination Significance")
    lines.append("=" * 72)
    lines.append("")

    # Pilot summary
    lines.append("## Pilot Data Summary")
    lines.append(f"  Tasks: {p.n_tasks}  |  Reps per cell: {p.n_reps_per_cell}")
    lines.append("")
    lines.append(
        f"  {'Task':<45} {'Baseline':>9} {'Hybrid':>9} {'Diff':>8} {'d_indep':>8}"
    )
    lines.append("  " + "-" * 80)
    for i, tid in enumerate(p.task_ids):
        d_str = (
            f"{p.per_task_cohens_d[i][1]:.3f}"
            if p.per_task_cohens_d[i][1] is not None
            else "N/A"
        )
        lines.append(
            f"  {tid:<45} {p.baseline_means[i]:>9.4f} "
            f"{p.hybrid_means[i]:>9.4f} {p.differences[i]:>8.4f} {d_str:>8}"
        )
    lines.append("  " + "-" * 80)
    lines.append(
        f"  {'Mean':<45} {np.mean(p.baseline_means):>9.4f} "
        f"{np.mean(p.hybrid_means):>9.4f} {p.mean_difference:>8.4f}"
    )
    lines.append(f"  SD of differences: {p.sd_difference:.4f}")
    dz_str = f"{p.cohens_dz:.3f}" if p.cohens_dz is not None else "N/A"
    lines.append(f"  Cohen's d_z (paired): {dz_str}")
    lines.append("")

    # Power results
    for label, r in [
        ("Paired t-test", analysis.paired_ttest),
        ("Wilcoxon signed-rank", analysis.wilcoxon),
    ]:
        lines.append(f"## {label}")
        lines.append(f"  Alpha: {r.alpha}  |  Target power: {r.target_power}")
        dz_display = f"{r.effect_size_dz:.3f}" if r.effect_size_dz is not None else "N/A"
        lines.append(f"  Effect size (d_z): {dz_display}")
        if r.required_n is not None:
            lines.append(f"  Required n: {r.required_n} tasks")
        else:
            lines.append("  Required n: indeterminate (see note)")
        if r.achievable_power_at_112 is not None:
            lines.append(
                f"  Power at n=112: {r.achievable_power_at_112:.3f}"
            )
        if r.achievable_power_at_n_tasks is not None:
            lines.append(
                f"  Power at n={TOTAL_TASKS}: {r.achievable_power_at_n_tasks:.3f}"
            )
        if r.note:
            lines.append(f"  Note: {r.note}")
        lines.append("")

    # Stratified plan
    sp = analysis.stratified_plan
    lines.append("## Stratified Sampling Plan")
    lines.append(f"  Target n: {sp.required_n}  |  Available: {sp.available_n}")
    lines.append(f"  Total allocated: {sp.total_allocated}")
    lines.append("")
    lines.append(
        f"  {'Suite':<25} {'Stratum':<25} {'Available':>9} {'Allocated':>9}"
    )
    lines.append("  " + "-" * 70)
    for c in sp.cells:
        lines.append(
            f"  {c.suite:<25} {c.stratum:<25} {c.available:>9} {c.allocated:>9}"
        )
    lines.append("  " + "-" * 70)
    lines.append(f"  {sp.coverage_note}")
    lines.append("")

    # Recommendation
    lines.append("## Recommendation")
    for line in analysis.recommendation.split("\n"):
        lines.append(f"  {line}")
    lines.append("")

    return "\n".join(lines)


def format_json(analysis: FullAnalysis) -> str:
    """Format analysis as JSON."""
    data: dict[str, Any] = {
        "pilot": asdict(analysis.pilot),
        "paired_ttest": asdict(analysis.paired_ttest),
        "wilcoxon": asdict(analysis.wilcoxon),
        "stratified_plan": {
            "required_n": analysis.stratified_plan.required_n,
            "available_n": analysis.stratified_plan.available_n,
            "total_allocated": analysis.stratified_plan.total_allocated,
            "coverage_note": analysis.stratified_plan.coverage_note,
            "cells": [asdict(c) for c in analysis.stratified_plan.cells],
        },
        "recommendation": analysis.recommendation,
    }
    return json.dumps(data, indent=2)


# ---------------------------------------------------------------------------
# Methodology text for paper
# ---------------------------------------------------------------------------


def generate_methodology_section(analysis: FullAnalysis) -> str:
    """Generate a methodology section suitable for the Phase 8 paper."""
    p = analysis.pilot
    t = analysis.paired_ttest
    w = analysis.wilcoxon

    dz_str = f"{p.cohens_dz:.2f}" if p.cohens_dz is not None else "N/A"
    req_t = t.required_n if t.required_n else "N/A"
    req_w = w.required_n if w.required_n else "N/A"
    pow_112_t = f"{t.achievable_power_at_112:.2f}" if t.achievable_power_at_112 else "N/A"

    return f"""### Statistical Methodology: Power Analysis for Mode Discrimination

**Design.** EnterpriseBench evaluates agent performance under three tool-access
modes: *baseline* (no MCP tools), *mcp_only* (MCP tools only), and *hybrid*
(MCP + baseline capabilities). The primary comparison is hybrid vs. baseline,
using task-level mean scores as the unit of analysis. Each task is measured
under both conditions (within-subjects design), yielding paired differences.

**Pilot study.** A Phase 1 pilot ran {p.n_tasks} tasks with {p.n_reps_per_cell}
repetitions per task-mode cell ({p.n_tasks} x 3 modes x {p.n_reps_per_cell}
reps = {p.n_tasks * 3 * p.n_reps_per_cell} total scored runs). The observed
mean paired difference (hybrid - baseline) was {p.mean_difference:.4f} (SD =
{p.sd_difference:.4f}), yielding a paired Cohen's d_z = {dz_str}.

**Effect size.** Per-task independent-samples Cohen's d ranged from
{min(d for _, d in p.per_task_cohens_d if d is not None):.2f} to
{max(d for _, d in p.per_task_cohens_d if d is not None):+.2f}.
Two of four pilot tasks showed positive discrimination (d > 0.5);
two showed negative discrimination attributed to single-repo task design
(see Phase 1 analysis). The paired effect size (d_z = {dz_str}) reflects
this mixed signal.

**Power analysis.** We conducted a prospective power analysis using the pilot
effect size to determine the number of tasks required for the full benchmark
run. Parameters: alpha = {t.alpha}, target power = {t.target_power} (one-sided
tests, directional hypothesis: hybrid > baseline).

- *Paired t-test*: Required n = {req_t} tasks (non-central t distribution).
- *Wilcoxon signed-rank*: Required n = {req_w} tasks (Monte Carlo simulation,
  {10_000:,} iterations).

At the full benchmark size of {TOTAL_TASKS} tasks, the achievable power is
{pow_112_t} (paired t-test).

**Stratification.** Tasks are drawn via proportional stratified sampling across
7 enterprise workflow suites and 6 difficulty strata to ensure representation.
{"All " + str(TOTAL_TASKS) + " tasks are included." if (t.required_n or 0) <= TOTAL_TASKS and (w.required_n or 0) <= TOTAL_TASKS else "The required sample size exceeds available tasks; all tasks are included with a note on reduced power."}

**Caveats.** The pilot effect size is estimated from {p.n_tasks} tasks with
wide confidence intervals on d_z. The power analysis assumes the full task
set—authored with Phase 1 design guidelines (cross-repo investigation paths,
40%+ cross-repo checkpoint weight, calibrated discovery difficulty)—will
exhibit a similar or larger positive effect. If the true effect is smaller
than estimated, the benchmark may be underpowered; increasing repetitions
per task from {p.n_reps_per_cell} to 5 would partially compensate.
"""


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Power analysis for mode discrimination significance.",
    )
    parser.add_argument(
        "--pilot-csv",
        type=Path,
        default=PROJECT_ROOT / "results" / "phase1_pilot" / "summary_scored.csv",
        help="Path to pilot results CSV (default: results/phase1_pilot/summary_scored.csv)",
    )
    parser.add_argument(
        "--alpha",
        type=float,
        default=0.1,
        help="Significance level (default: 0.1)",
    )
    parser.add_argument(
        "--power",
        type=float,
        default=0.8,
        help="Target statistical power (default: 0.8)",
    )
    parser.add_argument(
        "--simulations",
        type=int,
        default=10_000,
        help="Number of Monte Carlo simulations for Wilcoxon (default: 10000)",
    )
    parser.add_argument(
        "--json",
        action="store_true",
        dest="json_output",
        help="Output machine-readable JSON",
    )
    parser.add_argument(
        "--methodology",
        action="store_true",
        help="Output paper methodology section only",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)

    if not args.pilot_csv.exists():
        print(
            f"Error: Pilot CSV not found at {args.pilot_csv}",
            file=sys.stderr,
        )
        return 1

    analysis = run_analysis(
        args.pilot_csv,
        alpha=args.alpha,
        target_power=args.power,
        n_simulations=args.simulations,
    )

    if args.methodology:
        print(generate_methodology_section(analysis))
    elif args.json_output:
        print(format_json(analysis))
    else:
        print(format_human(analysis))

    return 0


if __name__ == "__main__":
    sys.exit(main())
