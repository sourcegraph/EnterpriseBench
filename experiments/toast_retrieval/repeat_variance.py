"""Across-repeat variance per policy: is the mean the whole story?

A policy that is occasionally brilliant but occasionally misses a required
file may be worse infrastructure than a consistent one at the same mean --
run-to-run variance is a first-class readout of the repeat design, not noise
to average away. This reads every repeat directory that exists for each
policy and reports, per policy: each repeat's mean file_recall, the spread of
those means, and the per-task instability (how many tasks change their recall
between repeats, and by how much).

Only tasks present in every available repeat of a policy are compared, so a
partially completed repeat (an interrupted sweep resumes later) narrows the
task set instead of biasing the mean.

Usage:
    python3 repeat_variance.py
    python3 repeat_variance.py --json variance.json
"""

from __future__ import annotations

import argparse
import json
import statistics
from pathlib import Path

REPEAT_DIRS = {
    "toast1": ["reports", "reports_r2", "reports_r3"],
    "code_finder": [
        "reports_code_finder",
        "reports_code_finder_r2",
        "reports_code_finder_r3",
    ],
    "deepsearch": ["reports_deepsearch", "reports_deepsearch_r2", "reports_deepsearch_r3"],
    "claude_adapter": [
        "reports_claude_adapter",
        "reports_claude_adapter_r2",
        "reports_claude_adapter_r3",
    ],
    "claude_local": [
        "reports_claude_local",
        "reports_claude_local_r2",
        "reports_claude_local_r3",
    ],
}


def _recalls(directory: Path) -> dict[str, float]:
    return {
        report["task_id"]: report["ir_scores"]["file_recall"]
        for report in (json.loads(p.read_text()) for p in directory.glob("*.json"))
    }


def analyze(policy: str, dir_names: list[str]) -> dict | None:
    repeats = [_recalls(d) for d in map(Path, dir_names) if d.is_dir()]
    repeats = [r for r in repeats if r]
    if len(repeats) < 2:
        return None
    shared = sorted(set.intersection(*(set(r) for r in repeats)))
    if not shared:
        return None

    repeat_means = [
        round(statistics.mean(r[t] for t in shared), 3) for r in repeats
    ]
    per_task_spread = {
        t: round(max(r[t] for r in repeats) - min(r[t] for r in repeats), 3)
        for t in shared
    }
    unstable = {t: s for t, s in per_task_spread.items() if s > 0}
    return {
        "policy": policy,
        "n_repeats": len(repeats),
        "n_tasks_compared": len(shared),
        "repeat_means": repeat_means,
        "mean_of_means": round(statistics.mean(repeat_means), 3),
        "spread_of_means": round(max(repeat_means) - min(repeat_means), 3),
        "unstable_tasks": len(unstable),
        "mean_per_task_spread": round(
            statistics.mean(per_task_spread.values()), 3
        ),
        "worst_tasks": sorted(unstable, key=unstable.get, reverse=True)[:5],
        "worst_spreads": sorted(unstable.values(), reverse=True)[:5],
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--json", type=Path, default=None)
    args = parser.parse_args()

    results = []
    for policy, dirs in REPEAT_DIRS.items():
        row = analyze(policy, dirs)
        if row is None:
            print(f"{policy}: fewer than 2 repeats on disk, skipped")
            continue
        results.append(row)
        print(
            f"{policy}: {row['n_repeats']} repeats x {row['n_tasks_compared']} tasks; "
            f"means {row['repeat_means']} (spread {row['spread_of_means']}); "
            f"{row['unstable_tasks']} unstable tasks, "
            f"mean per-task spread {row['mean_per_task_spread']}"
        )
        if row["worst_tasks"]:
            worst = ", ".join(
                f"{t} ({s})" for t, s in zip(row["worst_tasks"], row["worst_spreads"])
            )
            print(f"  most unstable: {worst}")

    if args.json:
        args.json.write_text(json.dumps(results, indent=2) + "\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
