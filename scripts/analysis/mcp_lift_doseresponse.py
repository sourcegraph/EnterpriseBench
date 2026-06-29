#!/usr/bin/env python3
"""MCP-lift dose-response analysis (pre-registered).

The headline result is a *null*: MCP tool access does not beat the baseline
(mean per-task delta ≈ -0.11 across modes). The standard objection to any null
is "your instrument is blind — it would report a null even if a real effect
existed." This script answers that objection with a pre-registered
dose-response design that is falsifiable in both directions.

PRE-REGISTERED PREDICTION
-------------------------
Every task carries a hand-authored, pre-registered hypothesis
``[tool_access].expected_mcp_benefit ∈ {low, medium, high}`` recorded BEFORE any
run. If MCP genuinely helps where we predicted it would, the observed lift
(mcp_only/hybrid − baseline) must INCREASE monotonically across the ordered
strata low → medium → high. The Jonckheere–Terpstra (JT) test targets exactly
this ordered-alternative trend.

  * Reject H0 (monotone increasing trend, JT one-sided p < 0.05)
      → the instrument is NOT blind; it detects benefit exactly where
        pre-registered. The flat headline is then a statement about effect
        *magnitude*, not instrument sensitivity.
  * Fail to reject, WITH the controls below passing
      → the null is a property of the world, not of a dead instrument.

CONTROLS (anchor the instrument's sensitivity)
----------------------------------------------
  * Negative control — calibration tasks were spec'd with MCP-advantage < 0.05
    (docs/ARCHITECTURE.md). Their observed |mean delta| should sit inside that
    band. If calibration tasks show a large delta, the measurement is noisy.
  * Positive control — the CSB cross-check replicates a +0.144 fixed-model,
    tooling-attributable lift (EnterpriseBench-nop/dse). The same paired-delta
    instrument detects lift when lift exists; both reference bands are printed
    alongside the observed per-stratum deltas for direct comparison.

This is pure deterministic statistics over already-collected scores — no
re-runs, no model calls. Data source: results/analysis/score_analysis.json
(``per_task``, the canonical aggregate behind the headline null), joined to each
task's task.toml for ``expected_mcp_benefit`` and ``difficulty_stratum``.

Usage:
    python scripts/analysis/mcp_lift_doseresponse.py
    python scripts/analysis/mcp_lift_doseresponse.py --json results/analysis/mcp_doseresponse.json
"""

from __future__ import annotations

import argparse
import json
import math
import random
import statistics
import sys
from pathlib import Path
from typing import Any, Optional

try:
    import tomllib
except ModuleNotFoundError:  # Python < 3.11
    import tomli as tomllib  # type: ignore[no-redefine]

REPO_ROOT = Path(__file__).resolve().parents[2]

# Ordered dose levels (low → high). The pre-registered alternative is that the
# observed MCP lift increases along this order.
BENEFIT_ORDER = ("low", "medium", "high")
MCP_MODES = ("mcp_only", "hybrid")
BASELINE = "baseline"

# Reference bands (printed, not recomputed here — they come from independent
# studies and anchor the instrument's sensitivity).
CSB_POSITIVE_CONTROL = 0.144   # CSB fixed-model tooling-attributable lift (dse/nop)
CALIBRATION_NEG_BAND = 0.05    # calibration spec: MCP-advantage < 0.05 (ARCHITECTURE.md)


# ---------------------------------------------------------------------------
# Data loading + join
# ---------------------------------------------------------------------------


def load_per_task(score_analysis_path: Path) -> list[dict[str, Any]]:
    """Load the per-task per-mode score records from score_analysis.json."""
    data = json.loads(score_analysis_path.read_text())
    per_task = data.get("per_task")
    if not isinstance(per_task, list) or not per_task:
        raise ValueError(
            f"{score_analysis_path} has no usable 'per_task' list "
            f"(got {type(per_task).__name__})"
        )
    return per_task


def index_task_tomls(benchmarks_root: Path) -> dict[str, tuple[Path, bool]]:
    """Map task_id -> (task.toml path, is_archived).

    Keyed by the authoritative ``[task].id`` inside each file — the directory
    name is NOT the task id (e.g. dir ``calibration-002`` holds id ``cal-...``).

    The _archived tree IS indexed: the score_analysis snapshot behind the
    headline null includes tasks that have since been archived, and
    ``expected_mcp_benefit`` is a pre-registration recorded at RUN time — a
    property of the run, not of current archive status. Excluding archived tasks
    would silently drop paired observations (including the entire ``low``
    stratum) and bias the rebuttal. Archive status is carried through so it can
    be reported, not hidden. A live file always wins over an archived file that
    somehow shares an id.
    """
    index: dict[str, tuple[Path, bool]] = {}
    for toml_path in benchmarks_root.rglob("task.toml"):
        is_archived = "_archived" in toml_path.parts
        try:
            with open(toml_path, "rb") as f:
                tid = (tomllib.load(f).get("task") or {}).get("id")
        except (OSError, tomllib.TOMLDecodeError):
            continue
        if not tid:
            continue
        if tid not in index or not is_archived:
            index[tid] = (toml_path, is_archived)
    return index


def load_task_dose_metadata(toml_path: Path) -> dict[str, Optional[str]]:
    """Extract the two pre-registered join fields from a task.toml.

    expected_mcp_benefit lives under [tool_access]; difficulty_stratum is a
    top-level key.
    """
    with open(toml_path, "rb") as f:
        data = tomllib.load(f)
    tool_access = data.get("tool_access") or {}
    return {
        "expected_mcp_benefit": tool_access.get("expected_mcp_benefit"),
        "difficulty_stratum": data.get("difficulty_stratum"),
    }


def paired_deltas(
    per_task: list[dict[str, Any]],
    toml_index: dict[str, tuple[Path, bool]],
    mode: str,
) -> tuple[list[dict[str, Any]], list[str]]:
    """Build the per-task (mode − baseline) deltas joined to dose metadata.

    Returns (records, warnings). Each record is
    {task_id, delta, expected_mcp_benefit, difficulty_stratum, is_calibration,
    archived}. A task is skipped (with a warning) when it lacks either mode's
    score or has no joinable task.toml — never silently dropped.
    """
    records: list[dict[str, Any]] = []
    warnings: list[str] = []
    for entry in per_task:
        task_id = entry.get("task_id", "")
        scores = entry.get("scores") or {}
        if mode not in scores or BASELINE not in scores:
            warnings.append(
                f"{task_id}: missing {mode!r} or {BASELINE!r} score — skipped"
            )
            continue
        hit = toml_index.get(task_id)
        if hit is None:
            warnings.append(f"{task_id}: no task.toml in benchmarks/ — skipped")
            continue
        toml_path, archived = hit
        meta = load_task_dose_metadata(toml_path)
        records.append(
            {
                "task_id": task_id,
                "delta": float(scores[mode]) - float(scores[BASELINE]),
                "expected_mcp_benefit": meta["expected_mcp_benefit"],
                "difficulty_stratum": meta["difficulty_stratum"],
                "is_calibration": bool(entry.get("is_calibration")),
                "archived": archived,
            }
        )
    return records, warnings


# ---------------------------------------------------------------------------
# Statistics (deterministic — paired d_z, percentile bootstrap, Jonckheere)
# ---------------------------------------------------------------------------


def cohens_dz(deltas: list[float]) -> Optional[float]:
    """Paired Cohen's d_z = mean(delta) / sd(delta). None if undefined."""
    if len(deltas) < 2:
        return None
    sd = statistics.stdev(deltas)
    if sd == 0:
        return 0.0
    return round(statistics.mean(deltas) / sd, 4)


def bootstrap_ci(
    deltas: list[float],
    iters: int,
    seed: int,
    alpha: float = 0.05,
) -> Optional[tuple[float, float]]:
    """Percentile bootstrap CI for the mean delta. Deterministic given seed."""
    n = len(deltas)
    if n < 2:
        return None
    rng = random.Random(seed)
    means = []
    for _ in range(iters):
        resample = [deltas[rng.randrange(n)] for _ in range(n)]
        means.append(statistics.mean(resample))
    means.sort()
    lo = means[max(0, int((alpha / 2) * iters) - 1)]
    hi = means[min(iters - 1, int((1 - alpha / 2) * iters) - 1)]
    return (round(lo, 4), round(hi, 4))


def _normal_sf(z: float) -> float:
    """Upper-tail standard-normal survival function, 1 - Phi(z)."""
    return 0.5 * math.erfc(z / math.sqrt(2.0))


def jonckheere_terpstra(ordered_groups: list[list[float]]) -> dict[str, Any]:
    """Jonckheere–Terpstra test for a monotone INCREASING trend across the
    groups in the given order (low → high).

    Returns the JT statistic, large-sample z, and one-sided p (increasing
    alternative). Uses the standard normal approximation WITHOUT the tie
    correction term, which slightly inflates the variance — i.e. the p-value is
    conservative in the presence of tied deltas (common here, since many tasks
    have delta = 0). Mid-rank (0.5) handling is applied to ties within each
    Mann–Whitney count.
    """
    groups = [g for g in ordered_groups if g]
    k = len(groups)
    if k < 2:
        return {"applicable": False, "reason": "need >= 2 non-empty groups"}

    # J = sum over i<j of U_ij, where U_ij counts (x in g_i, y in g_j) with
    # y > x, plus 0.5 per tie.
    j_stat = 0.0
    for i in range(k):
        for j in range(i + 1, k):
            for x in groups[i]:
                for y in groups[j]:
                    if y > x:
                        j_stat += 1.0
                    elif y == x:
                        j_stat += 0.5

    sizes = [len(g) for g in groups]
    big_n = sum(sizes)
    mean_j = (big_n**2 - sum(n * n for n in sizes)) / 4.0
    var_j = (
        big_n**2 * (2 * big_n + 3)
        - sum(n**2 * (2 * n + 3) for n in sizes)
    ) / 72.0

    if var_j <= 0:
        return {"applicable": False, "reason": "degenerate variance"}

    z = (j_stat - mean_j) / math.sqrt(var_j)
    p_increasing = _normal_sf(z)
    return {
        "applicable": True,
        "j_statistic": round(j_stat, 2),
        "expected_j_under_h0": round(mean_j, 2),
        "z": round(z, 4),
        "p_increasing_one_sided": round(p_increasing, 6),
        "group_order": None,  # filled by caller
        "group_sizes": sizes,
        "variance_note": "no tie correction (conservative)",
    }


# ---------------------------------------------------------------------------
# Analysis assembly
# ---------------------------------------------------------------------------


def stratum_summary(
    records: list[dict[str, Any]],
    key: str,
    levels: tuple[str, ...],
    iters: int,
    seed: int,
) -> list[dict[str, Any]]:
    """Per-level mean delta + bootstrap CI + d_z, in the given level order."""
    out = []
    for offset, level in enumerate(levels):
        deltas = [r["delta"] for r in records if r.get(key) == level]
        out.append(
            {
                "level": level,
                "n": len(deltas),
                "mean_delta": round(statistics.mean(deltas), 4) if deltas else None,
                "median_delta": (
                    round(statistics.median(deltas), 4) if deltas else None
                ),
                # Vary the seed per level so independent strata don't share a
                # resampling sequence.
                "bootstrap_ci95": bootstrap_ci(deltas, iters, seed + offset),
                "cohens_dz": cohens_dz(deltas),
            }
        )
    return out


def analyze_mode(
    per_task: list[dict[str, Any]],
    toml_index: dict[str, Path],
    mode: str,
    iters: int,
    seed: int,
) -> dict[str, Any]:
    records, warnings = paired_deltas(per_task, toml_index, mode)
    all_deltas = [r["delta"] for r in records]

    # Dose-response over the pre-registered expected_mcp_benefit ordering.
    dose = stratum_summary(records, "expected_mcp_benefit", BENEFIT_ORDER, iters, seed)
    ordered_groups = [
        [r["delta"] for r in records if r["expected_mcp_benefit"] == lvl]
        for lvl in BENEFIT_ORDER
    ]
    jt = jonckheere_terpstra(ordered_groups)
    jt["group_order"] = list(BENEFIT_ORDER)

    # Negative control: calibration tasks should sit inside the < 0.05 band.
    calib = [r["delta"] for r in records if r["is_calibration"]]
    calib_mean_abs = round(abs(statistics.mean(calib)), 4) if calib else None

    # How many tasks could not be joined to a pre-registered benefit level.
    unlabeled = [r["task_id"] for r in records if r["expected_mcp_benefit"] is None]
    n_archived = sum(1 for r in records if r["archived"])

    return {
        "mode": mode,
        "n_paired": len(records),
        "n_archived_in_paired": n_archived,
        "overall_mean_delta": (
            round(statistics.mean(all_deltas), 4) if all_deltas else None
        ),
        "dose_response": dose,
        "jonckheere_terpstra": jt,
        "negative_control": {
            "n_calibration": len(calib),
            "mean_abs_delta": calib_mean_abs,
            "band": CALIBRATION_NEG_BAND,
            "within_band": (
                None if calib_mean_abs is None else calib_mean_abs < CALIBRATION_NEG_BAND
            ),
        },
        "unlabeled_task_ids": unlabeled,
        "warnings": warnings,
    }


# ---------------------------------------------------------------------------
# Reporting
# ---------------------------------------------------------------------------


def _fmt(x: Optional[float]) -> str:
    return "  n/a" if x is None else f"{x:+.4f}"


def print_report(result: dict[str, Any]) -> None:
    print("=" * 72)
    print("MCP-LIFT DOSE-RESPONSE  (pre-registered; positive null rebuttal)")
    print("=" * 72)
    print(
        f"Reference bands:  positive control (CSB) = +{CSB_POSITIVE_CONTROL:.3f}   "
        f"negative control (calibration) = <{CALIBRATION_NEG_BAND:.2f}"
    )
    for mode_result in result["modes"]:
        m = mode_result["mode"]
        print()
        print("-" * 72)
        print(
            f"MODE: {m}   (n_paired={mode_result['n_paired']}, "
            f"overall mean delta={_fmt(mode_result['overall_mean_delta'])})"
        )
        n_arch = mode_result.get("n_archived_in_paired", 0)
        if n_arch:
            print(
                f"  provenance: {n_arch}/{mode_result['n_paired']} paired tasks are "
                f"now archived (pre-registered labels still valid — run-time property)"
            )
        print("-" * 72)
        print(f"{'benefit':>8} | {'n':>3} | {'mean Δ':>8} | {'95% CI':>20} | {'d_z':>7}")
        for row in mode_result["dose_response"]:
            ci = row["bootstrap_ci95"]
            ci_s = "n/a" if ci is None else f"[{ci[0]:+.3f}, {ci[1]:+.3f}]"
            dz = "n/a" if row["cohens_dz"] is None else f"{row['cohens_dz']:+.3f}"
            print(
                f"{row['level']:>8} | {row['n']:>3} | {_fmt(row['mean_delta']):>8} | "
                f"{ci_s:>20} | {dz:>7}"
            )
        jt = mode_result["jonckheere_terpstra"]
        if jt.get("applicable"):
            verdict = (
                "TREND DETECTED (instrument not blind)"
                if jt["p_increasing_one_sided"] < 0.05
                else "no monotone trend"
            )
            print(
                f"  Jonckheere–Terpstra (low→high): J={jt['j_statistic']} "
                f"z={jt['z']:+.3f} p(increasing)={jt['p_increasing_one_sided']:.4f} "
                f"→ {verdict}"
            )
        else:
            print(f"  Jonckheere–Terpstra: not applicable ({jt.get('reason')})")
        nc = mode_result["negative_control"]
        if nc["mean_abs_delta"] is None:
            print("  Negative control: no calibration tasks in paired set")
        else:
            status = "WITHIN band ✓" if nc["within_band"] else "OUTSIDE band ✗"
            print(
                f"  Negative control: calibration |mean Δ|={nc['mean_abs_delta']:.4f} "
                f"(n={nc['n_calibration']}, band <{nc['band']}) → {status}"
            )
        if mode_result["unlabeled_task_ids"]:
            print(
                f"  NOTE: {len(mode_result['unlabeled_task_ids'])} paired task(s) "
                f"had no expected_mcp_benefit and were excluded from the trend."
            )
        if mode_result["warnings"]:
            print(f"  {len(mode_result['warnings'])} task(s) skipped (join/score gaps).")
    print()
    print("=" * 72)
    print("Interpretation: a flat headline + (no JT trend) + (calibration within")
    print("band) + (CSB positive control detectable) = the null is a property of")
    print("the world, not a blind instrument. A detected JT trend would instead")
    print("show MCP helps exactly where pre-registered — falsifying the null.")
    print("=" * 72)


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------


def run(
    score_analysis_path: Path,
    benchmarks_root: Path,
    iters: int,
    seed: int,
) -> dict[str, Any]:
    per_task = load_per_task(score_analysis_path)
    toml_index = index_task_tomls(benchmarks_root)
    modes = [
        analyze_mode(per_task, toml_index, mode, iters, seed) for mode in MCP_MODES
    ]
    return {
        "source": str(score_analysis_path),
        "n_tasks_in_source": len(per_task),
        "bootstrap_iters": iters,
        "seed": seed,
        "reference_bands": {
            "csb_positive_control": CSB_POSITIVE_CONTROL,
            "calibration_negative_band": CALIBRATION_NEG_BAND,
        },
        "modes": modes,
    }


def main(argv: Optional[list[str]] = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--score-analysis",
        type=Path,
        default=REPO_ROOT / "results" / "analysis" / "score_analysis.json",
        help="path to score_analysis.json (per_task source)",
    )
    parser.add_argument(
        "--benchmarks-root",
        type=Path,
        default=REPO_ROOT / "benchmarks",
        help="benchmarks/ root for task.toml join",
    )
    parser.add_argument("--bootstrap-iters", type=int, default=10000)
    parser.add_argument(
        "--seed", type=int, default=12345, help="RNG seed (reproducible bootstrap)"
    )
    parser.add_argument(
        "--json", type=Path, default=None, help="optional path to write full JSON result"
    )
    args = parser.parse_args(argv)

    if not args.score_analysis.exists():
        parser.error(f"score-analysis not found: {args.score_analysis}")

    result = run(
        args.score_analysis, args.benchmarks_root, args.bootstrap_iters, args.seed
    )
    print_report(result)

    if args.json:
        args.json.parent.mkdir(parents=True, exist_ok=True)
        args.json.write_text(json.dumps(result, indent=2) + "\n")
        print(f"\nWrote {args.json}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
