"""Pick representative traces: the runs a reader should look at, chosen by rule.

Hand-picking traces is how an anecdote gets smuggled into a controlled result,
so selection here is mechanical and the rule is recorded beside each trace:

* ``best`` / ``worst``  -- extreme file_recall within a condition.
* ``divergent``         -- the task where conditions disagree most, shown for
  every condition, because the interesting behaviour is where the arms split
  rather than where they all succeed.

Usage:
    python3 select_traces.py --reports reports_claude_code --out traces.json
"""

from __future__ import annotations

import argparse
import json
import statistics
import sys
from collections import defaultdict
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

import analyze_traces  # noqa: E402

#: Fields kept per trace. The full report stays on disk; a trace bundle that
#: inlined it would be unreadable and would duplicate the corpus.
_KEEP = (
    "task_id", "condition", "repeat", "status", "first_primitive",
    "primitive_phases", "sequence", "queries", "total_calls",
    "first_relevant_call", "sufficient_evidence_call", "files_read",
    "relevant_files_read", "irrelevant_files_read", "file_recall",
    "input_tokens", "output_tokens", "wall_seconds", "tool_seconds",
)


def _trace(summary: dict, reason: str) -> dict:
    trace = {key: summary.get(key) for key in _KEEP}
    trace["selected_because"] = reason
    return trace


def divergent_task(summaries: list[dict]) -> str | None:
    """Task with the widest spread of mean recall across conditions."""
    by_task: dict[str, dict[str, list[float]]] = defaultdict(lambda: defaultdict(list))
    for s in summaries:
        if s["file_recall"] is not None:
            by_task[s["task_id"]][s["condition"]].append(s["file_recall"])
    spreads = {
        task: max(means) - min(means)
        for task, conditions in by_task.items()
        # A spread over one condition is not a disagreement.
        if len(conditions) > 1
        for means in [[statistics.fmean(v) for v in conditions.values()]]
    }
    return max(spreads, key=spreads.get) if spreads else None


def select(summaries: list[dict]) -> dict:
    scored = [s for s in summaries if s["file_recall"] is not None]
    by_condition: dict[str, list[dict]] = defaultdict(list)
    for s in scored:
        by_condition[s["condition"]].append(s)

    traces = []
    for condition, runs in sorted(by_condition.items()):
        ordered = sorted(runs, key=lambda s: (s["file_recall"], -s["total_calls"]))
        traces.append(_trace(ordered[-1], f"highest recall in {condition}"))
        traces.append(_trace(ordered[0], f"lowest recall in {condition}"))

    task = divergent_task(scored)
    if task:
        for s in sorted(
            (s for s in scored if s["task_id"] == task),
            key=lambda s: (s["condition"], s["repeat"] or 0),
        ):
            traces.append(_trace(s, f"conditions disagree most on {task}"))

    # Budget exhaustion is an outcome; a trace bundle that showed only scored
    # runs would hide the arm that spent everything and submitted nothing.
    for s in summaries:
        if s["file_recall"] is None:
            traces.append(_trace(s, f"unscored run: {s.get('status')}"))

    return {"most_divergent_task": task, "n_traces": len(traces), "traces": traces}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--reports", type=Path, required=True)
    parser.add_argument("--out", type=Path, default=HERE / "traces.json")
    args = parser.parse_args()

    reports = analyze_traces.load_reports(args.reports)
    if not reports:
        print(f"no reports under {args.reports}", file=sys.stderr)
        return 2
    summaries = []
    for report in reports:
        summary = analyze_traces.summarize_run(report)
        summary["status"] = report.get("status")
        summaries.append(summary)

    bundle = select(summaries)
    args.out.write_text(json.dumps(bundle, indent=2) + "\n")
    print(f"{bundle['n_traces']} traces -> {args.out} "
          f"(most divergent task: {bundle['most_divergent_task']})")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
