"""Compare search policies on one table, and test whether they are additive.

The product question is not "which policy has the highest mean recall" -- it is
whether adding Toast-1 to the MCP server would find code the policies already
there miss. So this reports, alongside the aggregate metrics, the pairwise
complementarity: tasks each policy solves that the other does not, and what a
union of their answers would score.

Reads the per-task reports written by run_toast.py and run_mcp_policy.py, which
share a schema and were scored by the same IR functions against the same ground
truth extractor.

Usage:
    python3 compare_policies.py
    python3 compare_policies.py --json comparison.json
"""

from __future__ import annotations

import argparse
import json
import statistics
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]

from aligned_scoring import SCORING_CONTRACT, found

# Toast's directory is the nls_search mapping: its best of the three measured
# adapter mappings, so the comparison gives it its strongest showing.
POLICY_DIRS = {
    "toast1": Path("reports"),
    "code_finder": Path("reports_code_finder"),
    "deepsearch": Path("reports_deepsearch"),
    # Claude over the same adapter Toast used: the model x access ablation.
    "claude_adapter": Path("reports_claude_adapter"),
}


def _load(directory: Path) -> dict[str, dict]:
    """Load a report directory, refusing any sweep scored before the fix.

    Reports written before `aligned_scoring` landed carry file_recall from
    `compute_ir_scores` alone, which under-credits by a different amount per
    arm. Averaging one of those in with corrected ones produces a comparison
    that looks fine and is wrong, so it is an error rather than a warning.
    Rescore with `rescore_aligned.py`.
    """
    reports = {}
    stale = []
    for path in sorted(directory.glob("*.json")):
        report = json.loads(path.read_text())
        if report.get("scoring") != SCORING_CONTRACT:
            stale.append(path.name)
        reports[report["task_id"]] = report
    if stale:
        raise SystemExit(
            f"{directory}: {len(stale)} report(s) predate the "
            f"'{SCORING_CONTRACT}' scoring contract (e.g. {stale[0]}). "
            f"Run: python3 rescore_aligned.py {directory}"
        )
    return reports




def _aggregate(reports: dict[str, dict], task_ids: list[str]) -> dict:
    rows = [reports[t] for t in task_ids]
    mean = lambda fn: round(statistics.mean(fn(r) for r in rows), 3)
    summary = {
        "n": len(rows),
        "file_recall": mean(lambda r: r["ir_scores"]["file_recall"]),
        "recall_at_10": mean(lambda r: r["ir_scores"]["recall"]["10"]),
        "precision_at_5": mean(lambda r: r["ir_scores"]["precision"]["5"]),
        "mrr": mean(lambda r: r["ir_scores"]["mrr"]),
        "ndcg_at_10": mean(lambda r: r["ir_scores"]["ndcg"]["10"]),
        "mean_files_returned": mean(lambda r: len(r["retrieved_paths"])),
        "perfect": sum(1 for r in rows if r["ir_scores"]["file_recall"] == 1.0),
        "zero": sum(1 for r in rows if r["ir_scores"]["file_recall"] == 0.0),
        "empty": sum(1 for r in rows if not r["retrieved_paths"]),
    }
    latencies = [r["elapsed_seconds"] for r in rows if "elapsed_seconds" in r]
    if latencies:
        summary["median_seconds"] = round(statistics.median(latencies), 1)
    return summary


def _complementarity(a: dict, b: dict, task_ids: list[str]) -> dict:
    """Does policy `a` find required files that policy `b` misses?"""
    a_only = b_only = both = neither = 0
    union_recalls, a_recalls, b_recalls = [], [], []
    a_unique_tasks = []
    for task_id in task_ids:
        required = a[task_id]["required_files"]
        found_a = found(a[task_id]["retrieved_paths"], required)
        found_b = found(b[task_id]["retrieved_paths"], required)
        a_recalls.append(len(found_a) / len(required))
        b_recalls.append(len(found_b) / len(required))
        union_recalls.append(len(found_a | found_b) / len(required))
        if found_a - found_b:
            a_unique_tasks.append(a[task_id]["task_id"])
        if found_a and found_b:
            both += 1
        elif found_a:
            a_only += 1
        elif found_b:
            b_only += 1
        else:
            neither += 1
    return {
        "a_recall": round(statistics.mean(a_recalls), 3),
        "b_recall": round(statistics.mean(b_recalls), 3),
        "union_recall": round(statistics.mean(union_recalls), 3),
        "union_lift_over_best": round(
            statistics.mean(union_recalls) - max(statistics.mean(a_recalls), statistics.mean(b_recalls)),
            3,
        ),
        "tasks_only_a_found_something": a_only,
        "tasks_only_b_found_something": b_only,
        "tasks_both_found_something": both,
        "tasks_neither_found_anything": neither,
        "tasks_where_a_added_a_file_b_missed": len(a_unique_tasks),
        "examples": a_unique_tasks[:6],
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--json", type=Path, default=None)
    parser.add_argument(
        "--dir",
        action="append",
        metavar="NAME=PATH",
        help="compare these report dirs instead of the default sweep "
        "(repeatable, e.g. --dir toast1=reports_hard_toast)",
    )
    args = parser.parse_args()

    policy_dirs = POLICY_DIRS
    if args.dir:
        policy_dirs = dict(
            (spec.split("=", 1)[0], Path(spec.split("=", 1)[1])) for spec in args.dir
        )
    loaded = {name: _load(d) for name, d in policy_dirs.items() if d.is_dir()}
    missing = [n for n in policy_dirs if n not in loaded]
    if missing:
        print(f"note: no reports for {', '.join(missing)}")

    shared = sorted(set.intersection(*(set(v) for v in loaded.values())))
    print(f"comparing {len(shared)} tasks common to {', '.join(loaded)}\n")

    aggregates = {name: _aggregate(r, shared) for name, r in loaded.items()}
    cols = ["file_recall", "recall_at_10", "precision_at_5", "mrr", "ndcg_at_10",
            "mean_files_returned", "perfect", "zero", "median_seconds"]
    print(f"{'policy':14}" + "".join(f"{c:>19}" for c in cols))
    for name, agg in aggregates.items():
        print(f"{name:14}" + "".join(f"{agg.get(c, '-')!s:>19}" for c in cols))

    print("\nby repo count (file_recall)")
    strata = {}
    for rc in (1, 2, 3):
        ids = [t for t in shared if next(iter(loaded.values()))[t]["repo_count"] == rc]
        if not ids:
            continue
        strata[rc] = {n: _aggregate(r, ids)["file_recall"] for n, r in loaded.items()}
        cells = "".join(f"{strata[rc][n]:>16}" for n in loaded)
        print(f"  {rc} repo(s), n={len(ids):<3}" + cells)
    print(f"  {'':14}" + "".join(f"{n:>16}" for n in loaded))

    print("\ncomplementarity")
    pairs = {}
    names = list(loaded)
    for i, a in enumerate(names):
        for b in names[i + 1:]:
            result = _complementarity(loaded[a], loaded[b], shared)
            pairs[f"{a}+{b}"] = result
            print(
                f"  {a} + {b}: union={result['union_recall']} "
                f"(best single {max(result['a_recall'], result['b_recall'])}, "
                f"lift +{result['union_lift_over_best']}); "
                f"{a} added a file {b} missed on "
                f"{result['tasks_where_a_added_a_file_b_missed']}/{len(shared)} tasks"
            )

    if args.json:
        args.json.write_text(
            json.dumps(
                {"tasks": shared, "aggregates": aggregates, "strata": strata,
                 "pairs": pairs},
                indent=2,
            )
            + "\n"
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
