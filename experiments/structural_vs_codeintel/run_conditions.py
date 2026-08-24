"""Sweep the five tool conditions over the leak-free retrieval slice.

One unit of work is (condition, repeat, task). Everything except the condition's
tool surface and backend is supplied by ``toast_retrieval.run_claude_policy``
and is identical across conditions: model, sampling, system prompt, task text,
repo snapshot, 4-round budget, and the ``aligned_suffix_v1`` scorer.

Run ordering is shuffled across conditions within each repeat, under a seed
derived from the repeat index, so provider-side drift over the sweep's wall
clock cannot line up with any one condition. The seed is recorded in every
report, so the order is reproducible rather than merely random.

Caching is per (condition, repeat, task) file: an interrupted sweep resumes,
and a re-run costs nothing for work already done.

Usage:
    python3 run_conditions.py --dry-run                    # no model calls
    python3 run_conditions.py --outdir reports_pilot --repeats 3 \\
        --tasks pilot_tasks.json
"""

from __future__ import annotations

import argparse
import json
import random
import sys
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

HERE = Path(__file__).resolve().parent
COVERAGE = HERE / "index_coverage.json"
TOAST_DIR = HERE.parent / "toast_retrieval"
sys.path.insert(0, str(TOAST_DIR))

from run_claude_policy import _DEFAULT_MODEL, build_generate_fn, run_one  # noqa: E402

from backends import Instrumented  # noqa: E402
from conditions import (  # noqa: E402
    CONDITIONS,
    DECLARED_ASYMMETRIES,
    check_description_parity,
)


def _needs_code_intel(condition_name: str) -> bool:
    tools = {t["function"]["name"] for t in CONDITIONS[condition_name].tools}
    return bool(tools & {"definition", "references"})


def _code_intel_ineligible(candidates) -> set[str] | None:
    """Task ids whose repos have no proven precise index, or None if unprobed.

    Coverage recorded over a superset of the sweep is fine; a task the probe
    never covered is not, because "we did not look" must not read as "it is
    fine". Both cases are distinguished here rather than collapsed into an
    empty set, which would silently sweep the arm anyway.
    """
    if not COVERAGE.is_file():
        return None
    coverage = json.loads(COVERAGE.read_text())
    probed = set(coverage["stores"])
    wanted = {s for c in candidates for s in c["store_identifiers"]}
    if wanted - probed:
        return None
    return {e["task_id"] for e in coverage["code_intel_ineligible_tasks"]}


def report_path(outdir: Path, condition: str, repeat: int, task_id: str) -> Path:
    return outdir / condition / f"r{repeat}" / f"{task_id}.json"


def _units(candidates, condition_names, repeats: int, seed: int):
    """(condition, repeat, task) units, shuffled within each repeat."""
    units = []
    for repeat in range(1, repeats + 1):
        block = [
            (name, repeat, candidate)
            for name in condition_names
            for candidate in candidates
        ]
        random.Random(seed + repeat).shuffle(block)
        units.extend(block)
    return units


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--tasks", type=Path, default=HERE / "pilot_tasks.json")
    parser.add_argument("--outdir", type=Path, default=HERE / "reports")
    parser.add_argument("--model", default=_DEFAULT_MODEL)
    parser.add_argument("--repeats", type=int, default=3)
    parser.add_argument("--workers", type=int, default=4)
    parser.add_argument("--seed", type=int, default=20260823)
    parser.add_argument(
        "--conditions",
        default=",".join(CONDITIONS),
        help="comma-separated condition names",
    )
    parser.add_argument("--task", default=None, help="restrict to one task_id")
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="validate conditions, tasks and clones without calling the model",
    )
    args = parser.parse_args()

    # A coached description would make every downstream number a measurement of
    # the prompt, so the gate runs before the sweep, not in a test someone may
    # not have run.
    problems = check_description_parity()
    if problems:
        print("tool description parity FAILED:", file=sys.stderr)
        for problem in problems:
            print(f"  - {problem}", file=sys.stderr)
        return 2

    condition_names = [n.strip() for n in args.conditions.split(",") if n.strip()]
    unknown = [n for n in condition_names if n not in CONDITIONS]
    if unknown:
        print(f"unknown conditions: {unknown}", file=sys.stderr)
        return 2

    candidates = json.loads(args.tasks.read_text())
    if args.task:
        candidates = [c for c in candidates if c["task_id"] == args.task]
        if not candidates:
            print(f"{args.task!r} not in {args.tasks}", file=sys.stderr)
            return 2

    # A code-intel run against an unindexed mirror would return nothing and
    # post a clean-looking zero for a task the arm was never given the means to
    # answer. preflight_index.py establishes coverage up front; those units are
    # dropped from the sweep rather than scored. Missing coverage is itself a
    # stop, not a shrug -- an unprobed corpus cannot say the arm is eligible.
    ineligible = _code_intel_ineligible(candidates)
    if ineligible is None:
        print(
            f"{COVERAGE.name} missing; run preflight_index.py --tasks "
            f"{args.tasks} before sweeping the code-intel arm",
            file=sys.stderr,
        )
        return 2

    # Constructing every backend up front turns "ast-grep is not installed"
    # into a startup failure rather than a sweep of empty structural results.
    backends = {name: CONDITIONS[name].backend() for name in condition_names}

    units = [
        unit
        for unit in _units(candidates, condition_names, args.repeats, args.seed)
        if not (
            _needs_code_intel(unit[0]) and unit[2]["task_id"] in ineligible
        )
    ]
    pending = [
        unit
        for unit in units
        if not report_path(args.outdir, unit[0], unit[1], unit[2]["task_id"]).is_file()
    ]
    print(
        f"{len(candidates)} tasks x {len(condition_names)} conditions x "
        f"{args.repeats} repeats = {len(units)} runs "
        f"({len(units) - len(pending)} cached, {len(pending)} to run) "
        f"model={args.model} seed={args.seed}",
        file=sys.stderr,
    )
    if ineligible:
        print(
            f"  {len(ineligible)} task(s) excluded from the code-intel arm for "
            "lack of a proven precise index (invalid, not zero): "
            + ", ".join(sorted(ineligible)),
            file=sys.stderr,
        )
    for asymmetry in DECLARED_ASYMMETRIES:
        print(f"  declared asymmetry: {asymmetry}", file=sys.stderr)
    if args.dry_run:
        for name in condition_names:
            tools = [t["function"]["name"] for t in CONDITIONS[name].tools]
            print(f"  {name}: {tools}", file=sys.stderr)
        return 0

    generate = {
        name: build_generate_fn(args.model, tools=CONDITIONS[name].tools)
        for name in condition_names
    }

    def work(unit) -> None:
        condition_name, repeat, candidate = unit
        task_id = candidate["task_id"]
        label = f"{condition_name}/r{repeat}/{task_id}"
        # A fresh recorder per run: the tool log belongs to one run, and these
        # execute concurrently.
        recorder = Instrumented(backends[condition_name])
        started = time.time()
        try:
            report = run_one(
                candidate,
                recorder,
                generate[condition_name],
                args.model,
                policy=f"condition:{condition_name}",
            )
        except Exception as exc:  # one run must not void the sweep
            print(f"{label}: FAILED -- {type(exc).__name__}: {exc}", file=sys.stderr)
            return
        condition = CONDITIONS[condition_name]
        report.update(
            condition=condition_name,
            condition_tools=[t["function"]["name"] for t in condition.tools],
            condition_knows=condition.knows,
            repeat=repeat,
            seed=args.seed,
            tool_log=recorder.to_log(),
            tool_seconds=round(sum(c.seconds for c in recorder.calls), 3),
            retrieval_result_chars=sum(c.result_chars for c in recorder.calls),
            wall_seconds=round(time.time() - started, 1),
        )
        destination = report_path(args.outdir, condition_name, repeat, task_id)
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_text(json.dumps(report, indent=2) + "\n")
        scores = report["ir_scores"]
        print(
            f"{label}: recall={scores['file_recall']:.3f} "
            f"overlap={scores['n_overlap']}/{scores['n_ground_truth']} "
            f"calls={len(report['tool_log'])} rounds={report['rounds_executed']} "
            f"{report['wall_seconds']}s",
            file=sys.stderr,
        )

    with ThreadPoolExecutor(max_workers=args.workers) as pool:
        list(pool.map(work, pending))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
