"""Tool-use behaviour, read independently of the headline retrieval score.

The brief's central distinction is that what agents *prefer*, what *retrieves*
best, what *finishes* the task, and what is *cheapest* are four different
claims. This module answers the first one, and it does so from the persisted
run reports alone -- no re-run, and no dependency on the score.

Every measure here is defined over the recorded ``tool_log`` (the calls that
actually reached a backend) plus the offered tool surface, so a condition that
never touches a tool it was given is visible as such rather than absent from
the data.

Primitive classes, kept separate because the systems know different things:

* ``lexical``    -- grep. Text.
* ``structural`` -- ast_search. Syntax.
* ``codeintel``  -- definition, references. Resolved symbol identity.
* ``navigation`` -- read_file, list_files. Not a search primitive; excluded
  from "first primitive chosen" and from transition counts, because a read is
  what every condition does after any of the three.

Usage:
    python3 analyze_traces.py --reports reports_pilot --json trace_stats.json
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from collections import Counter, defaultdict
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent / "toast_retrieval"))

from aligned_scoring import match_pairs, suffix_match  # noqa: E402

PRIMITIVE_CLASS = {
    "grep": "lexical",
    "ast_search": "structural",
    "definition": "codeintel",
    "references": "codeintel",
    "search_corpus": "lexical",
}
NAVIGATION = {"read_file", "list_files"}
SEARCH_CLASSES = ("lexical", "structural", "codeintel")

#: Arguments that carry the query a tool was issued with, in priority order.
_QUERY_KEYS = ("pattern", "symbol", "query")

#: ast-grep metavariables: `$NAME` for one node, `$$$NAME` for a sequence.
_METAVARIABLE = re.compile(r"\$\$?\$?[A-Z_][A-Z0-9_]*")


def query_of(entry: dict) -> str | None:
    arguments = entry.get("arguments") or {}
    for key in _QUERY_KEYS:
        value = arguments.get(key)
        if value:
            return str(value)
    return None


def _normalized(query: str) -> str:
    """Query reduced to its identifier-ish content, for near-duplicate counting.

    ``def $F($$$A)`` and ``def $NAME($$$ARGS)`` are the same search with
    different metavariable names, and counting them as distinct would report
    the structural arm as exploring more widely than it did.
    """
    # Metavariables go first: squashing punctuation would erase the `$` that
    # identifies them, leaving `F` and `NAME` to look like ordinary terms.
    stripped = _METAVARIABLE.sub(" ", query)
    tokens = "".join(c if c.isalnum() or c == "_" else " " for c in stripped).split()
    return " ".join(sorted({token.lower() for token in tokens}))


def summarize_run(report: dict) -> dict:
    """Behavioural summary of one run."""
    log = report.get("tool_log") or []
    offered = set(report.get("condition_tools") or [])
    required = report.get("required_files") or []

    sequence = [entry["tool"] for entry in log]
    search_sequence = [
        PRIMITIVE_CLASS[name] for name in sequence if name in PRIMITIVE_CLASS
    ]

    # Collapse consecutive repeats: three greps in a row are one lexical phase,
    # and counting them as two lexical->lexical transitions would drown the
    # cross-class moves this is here to find.
    phases: list[str] = []
    for klass in search_sequence:
        if not phases or phases[-1] != klass:
            phases.append(klass)

    transitions = Counter(zip(phases, phases[1:]))

    queries = [q for q in (query_of(entry) for entry in log) if q]
    normalized = [_normalized(q) for q in queries]
    exact_repeats = sum(count - 1 for count in Counter(queries).values() if count > 1)
    near_repeats = sum(count - 1 for count in Counter(normalized).values() if count > 1)

    offered_search = {name for name in offered if name in PRIMITIVE_CLASS}
    used = set(sequence)

    first_relevant, sufficient = _evidence_indices(log, required)
    read_paths = [
        (entry.get("arguments") or {}).get("path")
        for entry in log
        if entry["tool"] == "read_file"
    ]
    read_paths = [p for p in read_paths if p]
    relevant_reads = {
        p for p in read_paths if any(suffix_match(r, p) for r in required)
    }

    return {
        "task_id": report.get("task_id"),
        "condition": report.get("condition"),
        "repeat": report.get("repeat"),
        "calls_by_tool": dict(Counter(sequence)),
        "total_calls": len(sequence),
        "sequence": sequence,
        "primitive_sequence": search_sequence,
        "primitive_phases": phases,
        "first_primitive": phases[0] if phases else None,
        "first_tool": sequence[0] if sequence else None,
        "transitions": {f"{a}->{b}": n for (a, b), n in transitions.items()},
        "escalated_from_lexical": _escalated(phases, "lexical"),
        "queries": queries,
        "distinct_queries": len(set(queries)),
        "exact_repeat_queries": exact_repeats,
        "near_repeat_queries": near_repeats,
        # A richer tool offered and never touched. This is the preference
        # signal the brief asks for, and it only exists in the conditions that
        # offered a choice.
        "offered_search_tools": sorted(offered_search),
        "unused_search_tools": sorted(offered_search - used),
        "first_relevant_call": first_relevant,
        "sufficient_evidence_call": sufficient,
        "files_read": len(read_paths),
        "relevant_files_read": len(relevant_reads),
        "irrelevant_files_read": len(set(read_paths)) - len(relevant_reads),
        "tool_seconds": report.get("tool_seconds"),
        "retrieval_result_chars": report.get("retrieval_result_chars"),
        "file_recall": (report.get("ir_scores") or {}).get("file_recall"),
        "rounds_executed": report.get("rounds_executed"),
        "input_tokens": report.get("input_tokens"),
        "output_tokens": report.get("output_tokens"),
        "wall_seconds": report.get("wall_seconds"),
    }


def _escalated(phases: list[str], start: str) -> str | None:
    """The class an agent that began with *start* moved to first, if any."""
    if not phases or phases[0] != start:
        return None
    for klass in phases[1:]:
        if klass != start:
            return klass
    return None


def _evidence_indices(log: list[dict], required: list[str]):
    """1-based call index of first relevant evidence, and of sufficient evidence.

    "Seen" means a required file appeared in a tool result or was read -- the
    same ``aligned_scoring`` matcher the headline metric uses, so a file is not
    counted as found here under looser rules than it is scored under.
    """
    if not required:
        return None, None
    outstanding = set(required)
    first = None
    for index, entry in enumerate(log, start=1):
        seen = list(entry.get("hit_paths") or [])
        if not seen:
            continue
        matched = match_pairs(seen, sorted(outstanding))
        if not matched:
            continue
        if first is None:
            first = index
        outstanding -= set(matched.values())
        if not outstanding:
            return first, index
    return first, None


#: A run report is identified by these keys. The reports tree also accumulates
#: analysis output (comparison.json), and loading one of those as a run gives it
#: a `condition` of None, which crashes aggregation at best and silently invents
#: a condition at worst.
_REPORT_KEYS = ("condition", "task_id")


def load_reports(*directories: Path) -> list[dict]:
    """Run reports under one or more directories.

    Several directories are accepted because an ablation arm is swept into its
    own tree, and reading it beside the arm it is controlled against is the
    whole point of running it. Duplicate (condition, repeat, task) keys across
    trees are refused rather than merged: two files claiming the same run are
    a mistake somewhere, and averaging them would hide it.
    """
    reports: dict[tuple, dict] = {}
    for directory in directories:
        for path in sorted(directory.rglob("*.json")):
            payload = json.loads(path.read_text())
            if not isinstance(payload, dict):
                continue
            if not all(payload.get(key) for key in _REPORT_KEYS):
                continue
            key = (payload["condition"], payload.get("repeat"), payload["task_id"])
            if key in reports:
                raise ValueError(f"{path} duplicates run {key}")
            reports[key] = payload
    return list(reports.values())


def aggregate(summaries: list[dict]) -> dict:
    """Condition-level tool-use statistics."""
    by_condition: dict[str, list[dict]] = defaultdict(list)
    for summary in summaries:
        by_condition[summary["condition"]].append(summary)

    out = {}
    for condition, runs in sorted(by_condition.items()):
        first = Counter(r["first_primitive"] for r in runs if r["first_primitive"])
        transitions: Counter = Counter()
        for run in runs:
            transitions.update(run["transitions"])
        calls: Counter = Counter()
        for run in runs:
            calls.update(run["calls_by_tool"])
        never_used = Counter()
        for run in runs:
            never_used.update(run["unused_search_tools"])
        escalation = Counter(
            r["escalated_from_lexical"] for r in runs if r["escalated_from_lexical"]
        )
        out[condition] = {
            "n_runs": len(runs),
            "first_primitive": dict(first),
            "calls_by_tool": dict(calls),
            "transitions": dict(transitions),
            "escalation_from_lexical": dict(escalation),
            # Counted over runs whose condition offered the tool, so a tool no
            # condition offered cannot look unpopular.
            "offered_but_never_used": {
                tool: {
                    "runs_offered": sum(
                        1 for r in runs if tool in r["offered_search_tools"]
                    ),
                    "runs_unused": count,
                }
                for tool, count in sorted(never_used.items())
            },
            "mean_total_calls": _mean(r["total_calls"] for r in runs),
            "mean_exact_repeat_queries": _mean(r["exact_repeat_queries"] for r in runs),
            "mean_near_repeat_queries": _mean(r["near_repeat_queries"] for r in runs),
            "mean_first_relevant_call": _mean(
                r["first_relevant_call"] for r in runs if r["first_relevant_call"]
            ),
            "runs_reaching_sufficient_evidence": sum(
                1 for r in runs if r["sufficient_evidence_call"]
            ),
            "mean_sufficient_evidence_call": _mean(
                r["sufficient_evidence_call"] for r in runs if r["sufficient_evidence_call"]
            ),
            "mean_irrelevant_files_read": _mean(r["irrelevant_files_read"] for r in runs),
            "mean_retrieval_result_chars": _mean(
                r["retrieval_result_chars"] for r in runs if r["retrieval_result_chars"]
            ),
            "mean_tool_seconds": _mean(r["tool_seconds"] for r in runs if r["tool_seconds"]),
        }
    return out


def _mean(values) -> float | None:
    collected = [v for v in values if v is not None]
    if not collected:
        return None
    return round(sum(collected) / len(collected), 3)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--reports", type=Path, nargs="+", required=True)
    parser.add_argument("--json", type=Path, default=HERE / "trace_stats.json")
    parser.add_argument("--runs-json", type=Path, default=HERE / "trace_runs.json")
    args = parser.parse_args()

    reports = load_reports(*args.reports)
    if not reports:
        print(f"no reports under {[str(p) for p in args.reports]}", file=sys.stderr)
        return 2
    summaries = [summarize_run(report) for report in reports]
    args.runs_json.write_text(json.dumps(summaries, indent=2) + "\n")
    stats = aggregate(summaries)
    args.json.write_text(json.dumps(stats, indent=2) + "\n")

    for condition, values in stats.items():
        print(
            f"{condition:20s} n={values['n_runs']:3d} "
            f"first={values['first_primitive']} "
            f"calls={values['mean_total_calls']} "
            f"transitions={values['transitions']}"
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
