#!/usr/bin/env python3
"""Closed-book leakage gate for EnterpriseBench tasks.

A task measures codebase understanding only if its checkpoints cannot be
satisfied without the repo. This gate detects the opposite: checkpoints whose
Tier-1 `checks/*.sh` grep patterns are already satisfiable by the text of
`instruction.md` alone. Such a checkpoint credits a "closed-book" agent that
reads nothing and pastes the prompt back — the failure mode where closed-book
runs can match or beat open-book on the verifier.

Method (fully offline: no repo, no Docker, no LLM):
  For each task, build the report artifact the checks expect, fill it with
    (a) an empty/irrelevant line   -> floor control
    (b) the task's own instruction -> pure closed-book regurgitation
  then run the real checks/*.sh and read their JSON scores.

  leaked checkpoint    = echo score >= 1.0 AND empty score < 1.0
                         (the prompt content, not always-pass, satisfies it)
  always-pass checkpoint = empty score >= 1.0
                         (a separate verifier bug: passes on a garbage report)

Gate: with --check, fail (exit 2) if any task has a leaked checkpoint not
recorded in the baseline. The baseline is a ratchet — as tasks are fixed, drop
them from it; new leakage never gets in.

Usage:
  verify_closed_book.py [TASK_DIR ...]                 # audit (default: all active)
  verify_closed_book.py --json
  verify_closed_book.py --check --baseline configs/closed_book_baseline.json
  verify_closed_book.py --update-baseline configs/closed_book_baseline.json
"""
from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
import tempfile
from dataclasses import dataclass, field
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
BENCH = ROOT / "benchmarks"

# Any shell var assigned a path under $WORKSPACE (REPORT=, ANSWER=, OUTPUT=, ...).
_PATH_RE = re.compile(r'=(?:"|\')?\$\{?WORKSPACE(?::-/workspace)?\}?(/[^"\'\s]+)')

EMPTY_REPORT = "no findings\n"


def task_label(task: Path) -> str:
    """Stable id for a task: path relative to benchmarks/ when it lives there,
    else the directory name (keeps the gate usable on ad-hoc/out-of-tree dirs)."""
    task = task.resolve()
    try:
        return str(task.relative_to(BENCH))
    except ValueError:
        return task.name


def report_rel_paths(task: Path) -> set[str]:
    """File paths (relative to WORKSPACE) the task's checks read as the artifact."""
    paths: set[str] = set()
    for sh in sorted((task / "checks").glob("*.sh")):
        for m in _PATH_RE.finditer(sh.read_text(errors="ignore")):
            p = m.group(1).strip().strip("\"'")
            if p and "." in Path(p).name:  # file-like, not a bare dir
                paths.add(p.lstrip("/"))
    return paths


def run_checks(task: Path, report_content: str) -> dict[str, float | str]:
    """Run every checks/*.sh with `report_content` at each artifact path."""
    rels = report_rel_paths(task)
    scores: dict[str, float | str] = {}
    if not rels:
        return scores
    with tempfile.TemporaryDirectory() as ws:
        wsp = Path(ws)
        for rel in rels:
            dst = wsp / rel
            dst.parent.mkdir(parents=True, exist_ok=True)
            dst.write_text(report_content)
        env = dict(os.environ, WORKSPACE=str(wsp))
        for sh in sorted((task / "checks").glob("*.sh")):
            try:
                out = subprocess.run(
                    ["bash", str(sh)], env=env,
                    capture_output=True, text=True, timeout=60,
                )
                last = out.stdout.strip().splitlines()[-1] if out.stdout.strip() else ""
                scores[sh.name] = float(json.loads(last).get("score"))
            except Exception as e:  # noqa: BLE001 — surface, never silently drop
                scores[sh.name] = f"ERR:{e}"
    return scores


@dataclass(frozen=True)
class TaskLeakage:
    task: str
    n_checks: int
    empty: dict[str, float | str]
    echo: dict[str, float | str]
    leaked: tuple[str, ...] = field(default_factory=tuple)
    always_pass: tuple[str, ...] = field(default_factory=tuple)

    @property
    def echo_mean(self) -> float | None:
        vals = [v for v in self.echo.values() if isinstance(v, (int, float))]
        return round(sum(vals) / len(vals), 3) if vals else None


def measure(task: Path) -> TaskLeakage | None:
    if not (task / "checks").is_dir() or not (task / "instruction.md").exists():
        return None
    if not report_rel_paths(task):
        return None
    empty = run_checks(task, EMPTY_REPORT)
    echo = run_checks(task, (task / "instruction.md").read_text(errors="ignore"))
    leaked, always_pass = [], []
    for name, e_score in echo.items():
        empty_score = empty.get(name)
        if isinstance(empty_score, (int, float)) and empty_score >= 1.0:
            always_pass.append(name)
        elif isinstance(e_score, (int, float)) and e_score >= 1.0:
            leaked.append(name)
    return TaskLeakage(
        task=task_label(task),
        n_checks=len(echo),
        empty=empty, echo=echo,
        leaked=tuple(sorted(leaked)),
        always_pass=tuple(sorted(always_pass)),
    )


def discover_tasks(paths: list[Path]) -> list[Path]:
    if paths:
        return [p for p in paths if (p / "checks").is_dir()]
    tasks = []
    for toml in sorted(BENCH.rglob("task.toml")):
        if "_archived" not in toml.parts:
            tasks.append(toml.parent)
    return tasks


def audit(paths: list[Path]) -> list[TaskLeakage]:
    return [r for r in (measure(t) for t in discover_tasks(paths)) if r is not None]


def load_baseline(path: Path) -> dict[str, list[str]]:
    if not path.exists():
        return {}
    data = json.loads(path.read_text())
    return data.get("leaked_checkpoints", data)


def build_baseline(results: list[TaskLeakage]) -> dict:
    return {
        "_comment": "Closed-book leakage ratchet. Task -> checkpoints answerable "
                    "from the prompt alone. Shrink this as tasks are fixed; "
                    "verify_closed_book.py --check fails on any leak not listed here.",
        "leaked_checkpoints": {
            r.task: list(r.leaked) for r in sorted(results, key=lambda r: r.task) if r.leaked
        },
    }


def print_report(results: list[TaskLeakage]) -> None:
    n = len(results)
    fully = [r for r in results if r.echo_mean == 1.0]
    anyleak = [r for r in results if r.leaked]
    floorbugs = [r for r in results if r.always_pass]
    print(f"Closed-book leakage audit — {n} tasks with Tier-1 checks\n")
    print("prompt-echo mean-score distribution:")
    for lo, hi, lbl in [(1.0, 1.01, "== 1.0  FULLY leaked"), (0.75, 1.0, "0.75-0.99"),
                        (0.5, 0.75, "0.50-0.74"), (0.25, 0.5, "0.25-0.49"), (0.0, 0.25, "0.00-0.24")]:
        b = [r for r in results if r.echo_mean is not None and lo <= r.echo_mean < hi]
        print(f"  {lbl:22s} {len(b):3d}  {'#' * len(b)}")
    print(f"\n  fully-leaked tasks:                    {len(fully)}")
    print(f"  tasks with >=1 leaked checkpoint:      {len(anyleak)}")
    print(f"  always-pass verifier bugs (empty>0):   {len(floorbugs)}")


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("tasks", nargs="*", type=Path, help="Task dirs (default: all active)")
    p.add_argument("--json", action="store_true", help="Emit JSON")
    p.add_argument("--check", action="store_true", help="Gate mode: fail on leakage beyond baseline")
    p.add_argument("--baseline", type=Path, default=ROOT / "configs" / "closed_book_baseline.json")
    p.add_argument("--update-baseline", type=Path, metavar="PATH", help="Write current leakage as baseline")
    args = p.parse_args(argv)

    results = audit(args.tasks)

    if args.update_baseline:
        args.update_baseline.write_text(json.dumps(build_baseline(results), indent=2) + "\n")
        print(f"Wrote baseline ({sum(1 for r in results if r.leaked)} leaky tasks): {args.update_baseline}")
        return 0

    if args.json:
        print(json.dumps([{
            "task": r.task, "n_checks": r.n_checks, "echo_mean": r.echo_mean,
            "leaked": list(r.leaked), "always_pass": list(r.always_pass),
        } for r in results], indent=2))
        return 0

    if args.check:
        baseline = load_baseline(args.baseline)
        new_leaks: list[tuple[str, list[str]]] = []
        for r in results:
            allowed = set(baseline.get(r.task, []))
            fresh = [c for c in r.leaked if c not in allowed]
            if fresh:
                new_leaks.append((r.task, fresh))
        if new_leaks:
            print("FAIL — closed-book leakage not in baseline "
                  f"({args.baseline.name}):\n", file=sys.stderr)
            for task, checks in new_leaks:
                print(f"  {task}", file=sys.stderr)
                for c in checks:
                    print(f"      leaked checkpoint: {c}", file=sys.stderr)
            print("\nA checkpoint is 'leaked' when the prompt text alone satisfies its "
                  "grep checks.\nFix the task (demand repo-specific evidence) or, if "
                  "intentional, update the baseline.", file=sys.stderr)
            return 2
        remaining = sum(len(v) for v in baseline.values())
        print(f"PASS — no closed-book leakage beyond baseline ({remaining} grandfathered).")
        return 0

    print_report(results)
    return 0


if __name__ == "__main__":
    sys.exit(main())
