"""Choose the pilot subset: cover every category, not maximise N.

The brief's pilot exists to find harness and tool-description problems before
the full sweep, so its job is breadth. Greedy set cover over the category
manifest, ties broken by task_id so the selection is reproducible and does not
depend on dict order.

Selection reads ONLY the category labels and the repo count -- never a score,
never a prior run. Picking pilot tasks by past performance would choose the
tasks a condition already looks good on.

Usage:
    python3 select_pilot.py --size 14 --out pilot_tasks.json
"""

from __future__ import annotations

import argparse
import json
import sys
from collections import Counter
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

import build_categories  # noqa: E402


def cover(manifest: dict, size: int) -> list[str]:
    tasks = manifest["tasks"]
    # Rare categories first: a 5-member category is the one a greedy pass on
    # raw gain would leave until it no longer fits.
    remaining = {
        category
        for category in manifest["category_counts"]
        if manifest["category_counts"][category] > 0
    }
    chosen: list[str] = []
    while remaining and len(chosen) < size:
        best = max(
            (t for t in sorted(tasks) if t not in chosen),
            key=lambda t: (len(remaining & set(tasks[t]["categories"])), -len(tasks[t]["categories"])),
        )
        gained = remaining & set(tasks[best]["categories"])
        if not gained:
            break
        chosen.append(best)
        remaining -= gained

    # Fill the remainder toward the categories still thinnest in the pilot, so
    # a per-category pilot cell has more than one task where the corpus allows.
    while len(chosen) < size:
        seen = Counter(c for t in chosen for c in tasks[t]["categories"])
        candidates = [t for t in sorted(tasks) if t not in chosen]
        if not candidates:
            break
        chosen.append(min(candidates, key=lambda t: (
            min((seen[c] for c in tasks[t]["categories"]), default=0),
            -len(tasks[t]["categories"]),
            t,
        )))
    return chosen


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--size", type=int, default=14)
    parser.add_argument("--categories", type=Path, default=HERE / "task_categories.json")
    parser.add_argument("--out", type=Path, default=HERE / "pilot_tasks.json")
    args = parser.parse_args()

    manifest = json.loads(args.categories.read_text())
    chosen = set(cover(manifest, args.size))
    # The same corpus build_categories labelled, or a task selected into the
    # pilot would be silently dropped for having been authored later.
    corpus = build_categories._corpus()
    selected = [t for t in corpus if t["task_id"] in chosen]
    if len(selected) != len(chosen):
        raise SystemExit(f"selected {len(selected)} of {len(chosen)} chosen tasks")
    args.out.write_text(json.dumps(selected, indent=2) + "\n")

    covered = Counter(
        c for t in selected for c in manifest["tasks"][t["task_id"]]["categories"]
    )
    print(f"{len(selected)} tasks -> {args.out.name}")
    for category, total in sorted(manifest["category_counts"].items(), key=lambda kv: -kv[1]):
        if total:
            print(f"  {category:28s} pilot={covered[category]:2d}  slice={total:2d}")
    still_empty = [c for c in manifest["coverage_gaps"]
                   if not manifest["category_counts"].get(c)]
    for category in still_empty:
        print(f"  {category:28s} STILL EMPTY -- no task exercises this axis")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
