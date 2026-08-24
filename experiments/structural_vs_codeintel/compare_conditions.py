"""Condition comparison: retrieval, behaviour, and cost, kept apart.

Four claims the brief insists are different, and which one table would collapse:

* what agents PREFER      -- analyze_traces.py
* what RETRIEVES best     -- file_recall / precision / F1 here
* what FINISHES the task  -- end-task reward; NOT measured at this layer, and
                             reported as absent rather than substituted for
* what is CHEAPEST        -- tokens, cost, wall time, tool time, retrieval
                             characters here

Uncertainty is a paired bootstrap over TASKS, not over runs: repeats of the
same task are not independent samples, and resampling them as if they were
would shrink every interval. Each task's per-condition score is first averaged
across its repeats, then whole tasks are resampled with replacement.

Usage:
    python3 compare_conditions.py --reports reports_pilot \\
        --categories task_categories.json --json comparison.json
"""

from __future__ import annotations

import argparse
import json
import random
import statistics
import sys
from collections import defaultdict
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent / "toast_retrieval"))

from aligned_scoring import SCORING_CONTRACT  # noqa: E402

from analyze_traces import load_reports  # noqa: E402

_BOOTSTRAP_SAMPLES = 10_000
#: Pinned so a reported interval is reproducible from the same reports.
_BOOTSTRAP_SEED = 20260823

#: `eb_metrics.compute_ir_scores` reports `precision`/`recall`/`f1` as dicts
#: keyed by k, and only `file_recall` as a file-level scalar. There is no
#: `file_precision` or `file_f1` in a report, so naming them here would silently
#: drop two of the three quantities the brief asks for -- the comparison would
#: print a precision column that was always blank. They are derived instead,
#: from the counts the scorer does record.
METRICS = ("file_recall", "file_precision", "file_f1")


def derive_file_metrics(reports: list[dict]) -> list[dict]:
    """Add file-level precision and F1 to each report's scores, in place.

    Set-based, matching how `file_recall` is defined: of the files submitted,
    what fraction were required, and the harmonic mean of that with recall. A
    run that submitted nothing has no precision to report, and gets None rather
    than a 0.0 that would read as a measured miss.
    """
    for report in reports:
        scores = report.get("ir_scores")
        if not scores:
            continue
        overlap = scores.get("n_overlap")
        retrieved = scores.get("n_retrieved")
        recall = scores.get("file_recall")
        if overlap is None or not retrieved or recall is None:
            scores.setdefault("file_precision", None)
            scores.setdefault("file_f1", None)
            continue
        precision = overlap / retrieved
        scores["file_precision"] = round(precision, 4)
        scores["file_f1"] = (
            round(2 * precision * recall / (precision + recall), 4)
            if precision + recall
            else 0.0
        )
    return reports
COST_FIELDS = (
    # Reported by the generation seam, not derived from a price table here: a
    # local table would drift from the provider's and quietly restate an
    # arm's cost.
    "cost_usd",
    "input_tokens",
    "output_tokens",
    "wall_seconds",
    "tool_seconds",
    "retrieval_result_chars",
)


def _per_task(reports: list[dict], field: str, scores: bool = True):
    """{condition: {task_id: mean over repeats}} for one metric."""
    collected: dict[str, dict[str, list[float]]] = defaultdict(lambda: defaultdict(list))
    for report in reports:
        value = (report.get("ir_scores") or {}).get(field) if scores else report.get(field)
        if value is None:
            continue
        collected[report["condition"]][report["task_id"]].append(float(value))
    return {
        condition: {task: statistics.mean(values) for task, values in tasks.items()}
        for condition, tasks in collected.items()
    }


def paired_bootstrap(
    left: dict[str, float], right: dict[str, float], samples: int = _BOOTSTRAP_SAMPLES
) -> dict | None:
    """Mean paired difference (left - right) with a 95% interval.

    Paired on the task, so between-task difficulty -- by far the largest source
    of variance in this corpus -- cancels instead of drowning the effect.
    """
    shared = sorted(set(left) & set(right))
    if len(shared) < 2:
        return None
    differences = [left[task] - right[task] for task in shared]
    observed = statistics.mean(differences)
    rng = random.Random(_BOOTSTRAP_SEED)
    means = []
    for _ in range(samples):
        resampled = [differences[rng.randrange(len(differences))] for _ in differences]
        means.append(statistics.mean(resampled))
    means.sort()
    low = means[int(0.025 * samples)]
    high = means[int(0.975 * samples) - 1]
    return {
        "n_tasks": len(shared),
        "mean_difference": round(observed, 4),
        "ci95": [round(low, 4), round(high, 4)],
        # An interval spanning zero is not evidence of no difference at this n;
        # it is the absence of evidence of one, and the report must say so.
        "excludes_zero": (low > 0) or (high < 0),
    }


def completion(scored: list[dict], unscored: list[dict]) -> dict[str, dict]:
    """Per-condition budget-exhaustion and exclusion rates.

    A run that spent its whole turn budget without submitting is not a harness
    fault the way a disabled account is: the budget is a stated control, and
    exhausting it is something the arm did. Dropping those runs and reporting
    the mean over the rest is a survivorship statistic -- it removes an arm's
    worst cases and flatters it. So the rate is computed here and carried beside
    every quality mean, which are therefore means CONDITIONAL ON COMPLETION.

    Observed in a smoke run: the structural arm issued 61 ast_search calls over
    41 turns on one Envoy symbol task and never submitted. Scoring that 0.0
    would claim it retrieved nothing, which the tool log contradicts; silently
    excluding it would claim the arm never fails that way.
    """
    rows: dict[str, dict] = defaultdict(
        lambda: {"scored": 0, "excluded": defaultdict(int)}
    )
    for report in scored:
        rows[report["condition"]]["scored"] += 1
    for report in unscored:
        condition = report.get("condition", "unknown")
        rows[condition]["excluded"][report.get("status", "unknown")] += 1
    out = {}
    for condition, row in sorted(rows.items()):
        excluded = dict(row["excluded"])
        attempted = row["scored"] + sum(excluded.values())
        out[condition] = {
            "attempted": attempted,
            "scored": row["scored"],
            "completion_rate": round(row["scored"] / attempted, 4) if attempted else None,
            "excluded_by_status": excluded,
        }
    return out


def by_condition(
    reports: list[dict],
    categories: dict[str, list[str]],
    completion_rows: dict[str, dict] | None = None,
) -> dict:
    out: dict[str, dict] = {}
    per_condition: dict[str, list[dict]] = defaultdict(list)
    for report in reports:
        per_condition[report["condition"]].append(report)

    for condition, runs in sorted(per_condition.items()):
        row = {
            "n_runs": len(runs),
            "n_tasks": len({r["task_id"] for r in runs}),
            "knows": runs[0].get("condition_knows"),
            "metrics_are": "conditional on the run completing; see completion",
            "completion": (completion_rows or {}).get(condition),
        }
        for metric in METRICS:
            per_task = _per_task(runs, metric)
            values = list(per_task.get(condition, {}).values())
            row[metric] = round(statistics.mean(values), 4) if values else None
        for field in COST_FIELDS:
            values = [r.get(field) for r in runs if r.get(field) is not None]
            row[field] = round(statistics.mean(values), 2) if values else None
        # Run-to-run spread on the same task: the floor below which a
        # between-condition difference is indistinguishable from stochasticity.
        row["within_task_recall_sd"] = _within_task_sd(runs)
        row["by_category"] = _by_category(runs, categories)
        out[condition] = row
    return out


def _within_task_sd(runs: list[dict]) -> float | None:
    spreads = defaultdict(list)
    for run in runs:
        recall = (run.get("ir_scores") or {}).get("file_recall")
        if recall is not None:
            spreads[run["task_id"]].append(recall)
    sds = [statistics.stdev(v) for v in spreads.values() if len(v) > 1]
    return round(statistics.mean(sds), 4) if sds else None


def _by_category(runs: list[dict], categories: dict[str, list[str]]) -> dict:
    buckets: dict[str, list[float]] = defaultdict(list)
    for run in runs:
        recall = (run.get("ir_scores") or {}).get("file_recall")
        if recall is None:
            continue
        for category in categories.get(run["task_id"], ["uncategorized"]):
            buckets[category].append(recall)
    return {
        category: {
            "n_runs": len(values),
            "file_recall": round(statistics.mean(values), 4),
        }
        for category, values in sorted(buckets.items())
    }


def contrasts(reports: list[dict], categories: dict[str, list[str]]) -> dict:
    """Paired differences between every condition pair, overall and per category."""
    per_task = _per_task(reports, "file_recall")
    names = sorted(per_task)
    out = {}
    for i, left in enumerate(names):
        for right in names[i + 1 :]:
            result = paired_bootstrap(per_task[left], per_task[right])
            if result is None:
                continue
            result["by_category"] = {}
            for category in sorted({c for cs in categories.values() for c in cs}):
                members = {t for t, cs in categories.items() if category in cs}
                scoped = paired_bootstrap(
                    {t: v for t, v in per_task[left].items() if t in members},
                    {t: v for t, v in per_task[right].items() if t in members},
                )
                if scoped:
                    result["by_category"][category] = scoped
            out[f"{left} - {right}"] = result
    return out


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--reports", type=Path, nargs="+", required=True)
    parser.add_argument("--categories", type=Path, default=HERE / "task_categories.json")
    parser.add_argument("--json", type=Path, default=HERE / "comparison.json")
    args = parser.parse_args()

    reports = load_reports(*args.reports)
    if not reports:
        print(f"no reports under {[str(p) for p in args.reports]}", file=sys.stderr)
        return 2

    # A pre-fix sweep under a different scoring contract must never be averaged
    # in with corrected ones; the toast_retrieval comparison refuses the same way.
    wrong = {r.get("scoring") for r in reports} - {SCORING_CONTRACT}
    if wrong:
        print(f"reports with a foreign scoring contract: {wrong}", file=sys.stderr)
        return 2

    # Two generation seams exist (OpenRouter chat-completions, Claude Code CLI)
    # and they do not share sampling control, agent loop, or token accounting.
    # Averaging them would produce a number that describes neither, so a mixed
    # directory is refused rather than reconciled.
    seams = {r.get("generation_seam", "openrouter") for r in reports}
    if len(seams) > 1:
        print(
            f"reports mix generation seams {sorted(seams)}; compare them "
            "separately, or pass a directory holding one seam",
            file=sys.stderr,
        )
        return 2

    # A run the harness could not score (no submission, no index) carries
    # ir_scores=None on purpose. It is dropped here and counted, never folded
    # into the mean as a zero.
    derive_file_metrics(reports)
    unscored = [r for r in reports if r.get("ir_scores") is None]
    reports = [r for r in reports if r.get("ir_scores") is not None]
    if not reports:
        print(
            f"every report under {args.reports} is unscored "
            f"({len(unscored)} run(s)); nothing to compare",
            file=sys.stderr,
        )
        return 2
    if unscored:
        # Broken out per condition, not just per status. An exclusion that falls
        # evenly is noise; one that falls on a single arm is a biased filter on
        # the sample, and reporting only the total would hide exactly that.
        by_condition_status: dict[str, dict[str, int]] = defaultdict(
            lambda: defaultdict(int)
        )
        for report in unscored:
            by_condition_status[report.get("condition", "unknown")][
                report.get("status", "unknown")
            ] += 1
        print(f"excluded {len(unscored)} unscored run(s):", file=sys.stderr)
        for condition, statuses in sorted(by_condition_status.items()):
            print(f"  {condition:20s} {dict(statuses)}", file=sys.stderr)

    completion_rows = completion(reports, unscored)

    categories = {}
    if args.categories.is_file():
        manifest = json.loads(args.categories.read_text())
        categories = {
            task_id: entry["categories"] for task_id, entry in manifest["tasks"].items()
        }

    result = {
        "scoring": SCORING_CONTRACT,
        "bootstrap_samples": _BOOTSTRAP_SAMPLES,
        "bootstrap_seed": _BOOTSTRAP_SEED,
        "end_task_reward": (
            "not measured at this layer; this experiment scores retrieval only"
        ),
        "generation_seam": sorted(seams)[0],
        "unscored_runs_excluded": len(unscored),
        "completion": completion_rows,
        "conditions": by_condition(reports, categories, completion_rows),
        "contrasts": contrasts(reports, categories),
    }
    args.json.write_text(json.dumps(result, indent=2) + "\n")

    # `done` sits next to the quality columns on purpose. The means are
    # conditional on completion, so an arm with a low completion rate is being
    # scored on its surviving runs and the two numbers must be read together.
    header = (
        f"{'condition':20s} {'n':>4s} {'done':>6s} {'recall':>7s} {'prec':>6s} "
        f"{'F1':>6s} {'in_tok':>8s} {'out_tok':>8s} {'wall':>7s}"
    )
    print(header)
    for condition, row in result["conditions"].items():
        rate = (row.get("completion") or {}).get("completion_rate")
        print(
            f"{condition:20s} {row['n_runs']:4d} "
            f"{'--' if rate is None else format(rate, '6.2f')} "
            f"{row['file_recall'] or 0:7.3f} "
            f"{row['file_precision'] or 0:6.3f} {row['file_f1'] or 0:6.3f} "
            f"{row['input_tokens'] or 0:8.0f} {row['output_tokens'] or 0:8.0f} "
            f"{row['wall_seconds'] or 0:7.1f}"
        )
    print()
    for pair, values in result["contrasts"].items():
        marker = "*" if values["excludes_zero"] else " "
        print(
            f"{marker} {pair:45s} d={values['mean_difference']:+.3f} "
            f"CI95=[{values['ci95'][0]:+.3f}, {values['ci95'][1]:+.3f}] "
            f"n={values['n_tasks']}"
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
