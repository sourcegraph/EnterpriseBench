#!/usr/bin/env python3
"""Score analysis engine for EnterpriseBench.

Loads all benchmark results across modes, computes score distributions,
MCP benefit deltas, calibration bias checks, and statistical tests.
"""

from __future__ import annotations

import argparse
import json
import logging
import math
import re
import statistics
import sys
from dataclasses import dataclass, field, asdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

try:
    import tomllib
except ImportError:
    import tomli as tomllib  # type: ignore[no-redefine]

logger = logging.getLogger(__name__)

PROJECT_ROOT = Path(__file__).resolve().parent.parent

sys.path.insert(0, str(Path(__file__).resolve().parent / "lib"))
from run_status import is_scoreable  # noqa: E402

# ---------------------------------------------------------------------------
# Data types
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class Checkpoint:
    name: str
    weight: float
    score: float
    passed: bool


@dataclass(frozen=True)
class TaskResult:
    task_id: str
    mode: str
    success: bool
    task_score: float  # raw
    normalized_score: float  # task_score / checkpoints_total
    all_passed: bool
    checkpoints_passed: int
    checkpoints_total: int
    checkpoints: tuple[Checkpoint, ...]
    suite: str
    task_type: str
    difficulty: str
    languages: tuple[str, ...]
    agent_time: float | None  # seconds
    source_path: str


# ---------------------------------------------------------------------------
# Mode inference
# ---------------------------------------------------------------------------


def infer_mode(result_path: Path, data: dict[str, Any]) -> str:
    """Infer the run mode from directory structure and config."""
    # Check config.mode first
    config_mode = data.get("config", {}).get("mode")
    if config_mode:
        return config_mode

    # Infer from parent directory name
    parent = result_path.parent.name
    for suffix in ("_hybrid", "_mcp_only", "_baseline"):
        if parent.endswith(suffix):
            return suffix.lstrip("_")

    # Infer from grandparent directory pattern
    grandparent = result_path.parent.parent.name
    if grandparent.startswith("mcp_batch"):
        # Directory name should contain mode suffix
        for suffix in ("_hybrid", "_mcp_only", "_baseline"):
            if parent.endswith(suffix):
                return suffix.lstrip("_")
        # If in mcp_batch but no suffix, check if dirname contains mode hint
        if "hybrid" in parent:
            return "hybrid"
        if "mcp" in parent:
            return "mcp_only"

    # Multi-mode layout: results/runs/<task_id>/<mode>/results.json
    if parent in ("baseline", "mcp_only", "hybrid"):
        return parent

    # Default for results/runs/ (legacy single-mode layout)
    if "runs" in result_path.parts:
        return "baseline"

    # Smoke dirs
    if any(p.startswith("smoke_") for p in result_path.parts):
        for p in result_path.parts:
            if "hybrid" in p:
                return "hybrid"
            if "mcp" in p:
                return "mcp_only"

    return "baseline"


# ---------------------------------------------------------------------------
# Metadata fallback
# ---------------------------------------------------------------------------


def load_task_metadata_from_toml(task_id: str, benchmarks_root: Path) -> dict[str, Any]:
    """Search benchmarks/ for a matching task.toml and extract metadata."""
    for toml_path in benchmarks_root.rglob("task.toml"):
        if toml_path.parent.name == task_id:
            return _parse_toml_metadata(toml_path)
    return {}


def _parse_toml_metadata(path: Path) -> dict[str, Any]:
    """Parse task.toml and extract suite, task_type, difficulty, languages."""
    with open(path, "rb") as f:
        data = tomllib.load(f)

    meta: dict[str, Any] = {}
    task_section = data.get("task", {})
    for key in ("suite", "task_type", "difficulty"):
        if key in task_section:
            meta[key] = task_section[key]

    metadata_section = data.get("metadata", {})
    if "languages" in metadata_section:
        meta["languages"] = metadata_section["languages"]

    return meta


# ---------------------------------------------------------------------------
# Result loader
# ---------------------------------------------------------------------------


def parse_result(result_path: Path, benchmarks_root: Path) -> TaskResult | None:
    """Parse a single results.json into a TaskResult."""
    try:
        data = json.loads(result_path.read_text())
    except (json.JSONDecodeError, OSError) as exc:
        logger.warning("Skipping %s: %s", result_path, exc)
        return None

    task_id = data.get("task_id")
    if not task_id:
        logger.warning("No task_id in %s", result_path)
        return None

    # An invalidated run is dropped, not scored. Several branches
    # (``_route_zero_mcp_run``, ``_route_integrity_violation``,
    # ``_scan_mcp_config_error``) flag the run INVALID and then fall through to
    # scoring, so the file on disk carries a real-looking score. Under the
    # mcp_only filesystem gate the zero-MCP-call branch is the common one, and
    # counting its near-zero scores would push the gated arm down for a reason
    # that has nothing to do with retrieval quality.
    if not is_scoreable(
        data.get("status"), data.get("success", False), data.get("phase", "")
    ):
        logger.info(
            "Excluding INVALID run %s (phase=%s)", result_path, data.get("phase", "")
        )
        return None

    scores = data.get("scores")
    if not scores:
        logger.warning("No scores in %s", result_path)
        return None

    checkpoints_total = scores.get("checkpoints_total", 0)
    if checkpoints_total == 0:
        logger.warning("Zero checkpoints_total in %s", result_path)
        return None

    task_score = scores.get("task_score", 0.0)
    normalized = task_score / checkpoints_total

    checkpoints = tuple(
        Checkpoint(
            name=cp.get("name", ""),
            weight=cp.get("weight", 1.0),
            score=cp.get("score", 0.0),
            passed=cp.get("passed", False),
        )
        for cp in scores.get("checkpoints", [])
    )

    # Metadata: prefer task_metadata, fall back to task.toml
    tm = data.get("task_metadata", {})
    if not tm or not tm.get("suite"):
        tm = load_task_metadata_from_toml(task_id, benchmarks_root)

    mode = infer_mode(result_path, data)

    agent_time = data.get("timing", {}).get("agent")

    return TaskResult(
        task_id=task_id,
        mode=mode,
        success=data.get("success", False),
        task_score=task_score,
        normalized_score=normalized,
        all_passed=scores.get("all_passed", False),
        checkpoints_passed=scores.get("checkpoints_passed", 0),
        checkpoints_total=checkpoints_total,
        checkpoints=checkpoints,
        suite=tm.get("suite", "unknown"),
        task_type=tm.get("task_type", "unknown"),
        difficulty=tm.get("difficulty", "unknown"),
        languages=tuple(tm.get("languages", [])),
        agent_time=agent_time,
        source_path=str(result_path),
    )


def load_all_results(
    results_dirs: list[Path],
    benchmarks_root: Path,
) -> list[TaskResult]:
    """Scan all results dirs, parse, and deduplicate."""
    all_results: list[TaskResult] = []

    for rdir in results_dirs:
        if not rdir.exists():
            logger.debug("Results dir not found: %s", rdir)
            continue
        for rjson in rdir.rglob("results.json"):
            tr = parse_result(rjson, benchmarks_root)
            if tr is not None:
                all_results.append(tr)

    # Deduplicate: same task_id + mode -> keep highest score
    best: dict[tuple[str, str], TaskResult] = {}
    for tr in all_results:
        key = (tr.task_id, tr.mode)
        if key not in best or tr.normalized_score > best[key].normalized_score:
            best[key] = tr

    deduped = list(best.values())
    logger.info("Loaded %d results (%d after dedup)", len(all_results), len(deduped))
    return deduped


# ---------------------------------------------------------------------------
# Distribution helpers
# ---------------------------------------------------------------------------


def _dist_stats(results: list[TaskResult]) -> dict[str, Any]:
    """Compute distribution statistics for a list of TaskResults."""
    if not results:
        return {
            "count": 0,
            "mean": None,
            "median": None,
            "std": None,
            "min": None,
            "max": None,
            "pass_rate": None,
        }
    scores = [r.normalized_score for r in results]
    passed = sum(1 for r in results if r.all_passed)
    return {
        "count": len(scores),
        "mean": round(statistics.mean(scores), 4),
        "median": round(statistics.median(scores), 4),
        "std": round(statistics.stdev(scores), 4) if len(scores) > 1 else 0.0,
        "min": round(min(scores), 4),
        "max": round(max(scores), 4),
        "pass_rate": round(passed / len(results), 4),
    }


#: Arms the headline compares. ``cli`` is excluded for the same reason
#: ``run_sweep.DEFAULT_SWEEP_MODES`` excludes it: it is not part of this
#: comparison, and intersecting on it would silently shrink the subset.
HEADLINE_MODES = ("baseline", "mcp_only", "hybrid")


def by_mode(results: list[TaskResult]) -> dict[str, dict[str, Any]]:
    buckets: dict[str, list[TaskResult]] = {}
    for r in results:
        buckets.setdefault(r.mode, []).append(r)
    return {mode: _dist_stats(rs) for mode, rs in sorted(buckets.items())}


def matched_subset(
    results: list[TaskResult],
    modes: tuple[str, ...] = HEADLINE_MODES,
) -> list[TaskResult]:
    """Results for only those tasks that produced a valid run in *every* mode.

    Comparing per-arm means over each arm's own task set compares two different
    benchmarks and attributes the difference to the tool. The arms genuinely do
    not cover the same tasks: ``mode_gate.check_eligibility`` refuses
    ``code_patch`` tasks under ``mcp_only`` (they need local writes the gate
    denies), and any arm can lose a task to an invalidated run. Intersecting
    first means the headline varies the tool and nothing else.

    This subsumes the code_patch exclusion rather than special-casing it: an
    ineligible task never yields an ``mcp_only`` result, so it drops out of the
    intersection on its own.
    """
    by_task: dict[str, set[str]] = {}
    for r in results:
        by_task.setdefault(r.task_id, set()).add(r.mode)

    complete = {tid for tid, seen in by_task.items() if set(modes) <= seen}
    return [r for r in results if r.task_id in complete and r.mode in modes]


def by_group_and_mode(
    results: list[TaskResult],
    group_key: str,
) -> dict[str, dict[str, dict[str, Any]]]:
    """Group by a TaskResult attribute then by mode."""
    outer: dict[str, dict[str, list[TaskResult]]] = {}
    for r in results:
        grp = getattr(r, group_key)
        outer.setdefault(grp, {}).setdefault(r.mode, []).append(r)
    return {
        grp: {mode: _dist_stats(rs) for mode, rs in sorted(modes.items())}
        for grp, modes in sorted(outer.items())
    }


# ---------------------------------------------------------------------------
# MCP delta analysis
# ---------------------------------------------------------------------------


def _compute_delta(
    results: list[TaskResult],
    mcp_mode: str,
) -> dict[str, Any]:
    """Compute paired delta between an MCP mode and baseline."""
    baseline_map = {r.task_id: r for r in results if r.mode == "baseline"}
    mcp_map = {r.task_id: r for r in results if r.mode == mcp_mode}

    paired_ids = sorted(set(baseline_map) & set(mcp_map))
    if not paired_ids:
        return {"n_paired": 0}

    deltas = [
        mcp_map[tid].normalized_score - baseline_map[tid].normalized_score
        for tid in paired_ids
    ]

    n = len(deltas)
    improved = sum(1 for d in deltas if d > 0.001)
    degraded = sum(1 for d in deltas if d < -0.001)
    unchanged = n - improved - degraded

    mean_d = statistics.mean(deltas)
    median_d = statistics.median(deltas)

    result: dict[str, Any] = {
        "n_paired": n,
        "mean_delta": round(mean_d, 4),
        "median_delta": round(median_d, 4),
        "pct_improved": round(improved / n, 4),
        "pct_degraded": round(degraded / n, 4),
        "pct_unchanged": round(unchanged / n, 4),
    }

    # Statistical tests
    result.update(
        _statistical_tests(
            [baseline_map[tid].normalized_score for tid in paired_ids],
            [mcp_map[tid].normalized_score for tid in paired_ids],
        )
    )

    return result


def _statistical_tests(
    baseline_scores: list[float],
    mcp_scores: list[float],
) -> dict[str, Any]:
    """Wilcoxon signed-rank test and Cohen's d."""
    n = len(baseline_scores)
    # Cohen's d
    diffs = [m - b for b, m in zip(baseline_scores, mcp_scores)]
    mean_diff = statistics.mean(diffs)
    if n > 1:
        sd_diff = statistics.stdev(diffs)
        cohens_d = round(mean_diff / sd_diff, 4) if sd_diff > 0 else 0.0
    else:
        cohens_d = 0.0

    result: dict[str, Any] = {"cohens_d": cohens_d}

    try:
        from scipy.stats import wilcoxon  # type: ignore[import-untyped]

        # Wilcoxon needs at least some non-zero differences
        if any(abs(d) > 1e-9 for d in diffs) and n >= 6:
            stat, p_value = wilcoxon(diffs)
            result["wilcoxon_p"] = round(p_value, 6)
            result["significant"] = p_value < 0.05
        else:
            result["wilcoxon_p"] = None
            result["significant"] = False
            if n < 6:
                result["note"] = f"Too few pairs ({n}) for Wilcoxon test"
    except ImportError:
        logger.warning("scipy not installed — skipping Wilcoxon test")
        result["wilcoxon_p"] = None
        result["significant"] = None
        result["note"] = "scipy not installed"

    return result


def mcp_deltas(results: list[TaskResult]) -> dict[str, dict[str, Any]]:
    return {
        "hybrid_vs_baseline": _compute_delta(results, "hybrid"),
        "mcp_only_vs_baseline": _compute_delta(results, "mcp_only"),
    }


# ---------------------------------------------------------------------------
# Calibration bias
# ---------------------------------------------------------------------------


def calibration_bias(
    results: list[TaskResult],
    bias_threshold: float = 0.10,
) -> dict[str, Any]:
    """Check calibration tasks for mode bias."""
    cal_results = [r for r in results if r.task_id.startswith("cal-")]

    if not cal_results:
        return {
            "calibration_task_count": 0,
            "mean_by_mode": {},
            "max_mode_delta": None,
            "bias_flagged": False,
            "bias_threshold": bias_threshold,
        }

    mode_scores: dict[str, list[float]] = {}
    for r in cal_results:
        mode_scores.setdefault(r.mode, []).append(r.normalized_score)

    mean_by_mode = {
        mode: round(statistics.mean(scores), 4)
        for mode, scores in sorted(mode_scores.items())
    }

    means = list(mean_by_mode.values())
    max_delta = round(max(means) - min(means), 4) if len(means) > 1 else 0.0

    return {
        "calibration_task_count": len(cal_results),
        "mean_by_mode": mean_by_mode,
        "max_mode_delta": max_delta,
        "bias_flagged": max_delta > bias_threshold,
        "bias_threshold": bias_threshold,
    }


# ---------------------------------------------------------------------------
# Per-task summary
# ---------------------------------------------------------------------------


def per_task_summary(results: list[TaskResult]) -> list[dict[str, Any]]:
    """Build per-task cross-mode summary."""
    tasks: dict[str, dict[str, Any]] = {}
    for r in results:
        if r.task_id not in tasks:
            tasks[r.task_id] = {
                "task_id": r.task_id,
                "suite": r.suite,
                "difficulty": r.difficulty,
                "task_type": r.task_type,
                "scores": {},
                "checkpoints": {},
                "is_calibration": r.task_id.startswith("cal-"),
            }
        entry = tasks[r.task_id]
        entry["scores"][r.mode] = round(r.normalized_score, 4)
        entry["checkpoints"][r.mode] = {
            "passed": r.checkpoints_passed,
            "total": r.checkpoints_total,
        }

    return sorted(tasks.values(), key=lambda t: t["task_id"])


# ---------------------------------------------------------------------------
# Main analysis
# ---------------------------------------------------------------------------


def analyze(
    results_dirs: list[Path],
    benchmarks_root: Path,
) -> dict[str, Any]:
    results = load_all_results(results_dirs, benchmarks_root)
    matched = matched_subset(results)
    matched_ids = sorted({r.task_id for r in matched})

    return {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "total_results": len(results),
        # The headline. by_mode below is the same statistic over each arm's own
        # task set, kept for reference — it is not comparable across arms.
        "matched": {
            "modes": list(HEADLINE_MODES),
            "n_tasks": len(matched_ids),
            "task_ids": matched_ids,
            "by_mode": by_mode(matched),
            "mcp_delta": mcp_deltas(matched),
        },
        "by_mode": by_mode(results),
        "by_suite": by_group_and_mode(results, "suite"),
        "by_difficulty": by_group_and_mode(results, "difficulty"),
        "by_task_type": by_group_and_mode(results, "task_type"),
        "mcp_delta": mcp_deltas(results),
        "calibration_bias": calibration_bias(results),
        "per_task": per_task_summary(results),
    }


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def _default_results_dirs(project_root: Path) -> list[Path]:
    """Gather default results directories."""
    dirs = [project_root / "results" / "runs"]
    results_dir = project_root / "results"
    if results_dir.exists():
        for p in sorted(results_dir.iterdir()):
            if p.is_dir() and (
                p.name.startswith("mcp_batch") or p.name.startswith("smoke_")
            ):
                dirs.append(p)
    return dirs


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(
        description="Analyze EnterpriseBench scores across modes.",
    )
    parser.add_argument(
        "--results-dir",
        dest="results_dirs",
        action="append",
        type=Path,
        default=None,
        help="Results directory (repeatable). Defaults to runs + mcp_batch* + smoke_*.",
    )
    parser.add_argument(
        "--benchmarks-root",
        type=Path,
        default=Path("benchmarks"),
        help="Root of benchmark task definitions (default: benchmarks/).",
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=Path("results/analysis/score_analysis.json"),
        help="Output JSON path.",
    )
    parser.add_argument(
        "-v",
        "--verbose",
        action="store_true",
        help="Verbose logging.",
    )

    args = parser.parse_args(argv)

    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(levelname)s: %(message)s",
    )

    if args.results_dirs is None:
        args.results_dirs = _default_results_dirs(PROJECT_ROOT)

    logger.info("Results dirs: %s", [str(d) for d in args.results_dirs])
    logger.info("Benchmarks root: %s", args.benchmarks_root)

    report = analyze(args.results_dirs, args.benchmarks_root)

    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, indent=2, default=str) + "\n")
    logger.info("Wrote %s", args.output)

    # Print summary to stdout
    print(f"\n=== EnterpriseBench Score Analysis ===")
    print(f"Total results: {report['total_results']}")
    print()
    for mode, stats in report["by_mode"].items():
        print(
            f"  {mode:12s}  n={stats['count']:3d}  "
            f"mean={stats['mean']:.3f}  median={stats['median']:.3f}  "
            f"std={stats['std']:.3f}  pass_rate={stats['pass_rate']:.2f}"
        )
    print()

    delta = report["mcp_delta"]
    for label, key in [
        ("hybrid vs baseline", "hybrid_vs_baseline"),
        ("mcp_only vs baseline", "mcp_only_vs_baseline"),
    ]:
        d = delta[key]
        if d["n_paired"] > 0:
            print(
                f"  {label}: n={d['n_paired']}  "
                f"mean_delta={d['mean_delta']:+.3f}  "
                f"pct_improved={d['pct_improved']:.0%}  "
                f"cohens_d={d['cohens_d']:.3f}  "
                f"p={d.get('wilcoxon_p', 'N/A')}"
            )
        else:
            print(f"  {label}: no paired tasks")
    print()

    cb = report["calibration_bias"]
    if cb["calibration_task_count"] > 0:
        flag = "FLAGGED" if cb["bias_flagged"] else "OK"
        print(
            f"  Calibration bias: {flag} "
            f"(max_delta={cb['max_mode_delta']:.3f}, "
            f"threshold={cb['bias_threshold']:.2f}, "
            f"n={cb['calibration_task_count']})"
        )
    print()


if __name__ == "__main__":
    main()
