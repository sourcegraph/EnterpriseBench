#!/usr/bin/env python3
"""Authoring-time ecosystem balance gate.

Prevents new task additions from worsening ecosystem imbalance. Compares
the multi-repo ecosystem distribution between a baseline (committed state)
and the candidate (working tree, staged changes, or another path) and
blocks when any capped ecosystem ends up over its cap *and* the candidate
made the situation worse than the baseline.

Behavior summary:

- Adding a Go task while Go% is already over the 40% cap        -> BLOCK
- Adding a non-Go task while Go% is over cap (dilutes Go%)      -> PASS
- Adding a Go task that pushes Go% from 39% -> 40.5%            -> BLOCK
- Removing a non-Go task such that Go% rises further over cap   -> BLOCK
- Adding any task while no capped ecosystem is over cap         -> PASS

This separates "we are already over cap (legacy state)" from "this commit
is making it worse" so that work in progress (like the 0rv.azt mass
non-Go authoring) is not blocked by the cap, but Go-heavy bursts are.

Cap source: see docs/benchmark_design.md (canonical value is also
expressed in scripts/validation/task_mix_validator.MAX_SINGLE_ECOSYSTEM_PCT,
currently 0.40 for the Go ecosystem on multi-repo tasks).

Usage:
    # Default: compare working tree vs HEAD
    python3 scripts/validation/ecosystem_cap_gate.py

    # JSON output (for hook scripts)
    python3 scripts/validation/ecosystem_cap_gate.py --json

    # Compare two task directories directly (for tests)
    python3 scripts/validation/ecosystem_cap_gate.py \
        --baseline-tasks-dir /tmp/before \
        --candidate-tasks-dir /tmp/after

Exit codes:
    0 - gate passes (no new violations introduced)
    1 - gate fails (commit would worsen a capped ecosystem)
    2 - usage / IO error
"""

from __future__ import annotations

import argparse
import json
import shutil
import subprocess
import sys
import tempfile
from collections import Counter
from dataclasses import dataclass
from pathlib import Path
from typing import Any

try:
    import tomllib
except ImportError:  # pragma: no cover - python <3.11 fallback
    import tomli as tomllib  # type: ignore[no-redef]


PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent

# Multi-repo strata that count toward the ecosystem-cap denominator.
ALL_MULTI_REPO_STRATA = frozenset(
    {"dual_repo", "tri_repo", "multi_repo", "monorepo_cross_package"}
)

# Default caps (ecosystem -> max share of multi-repo tasks). Only "go" is
# enforced today, per docs/benchmark_design.md and the PRD task-mix
# realignment doc. The structure is extensible; add new ecosystems here
# if/when imbalance shows up elsewhere.
DEFAULT_CAPS: dict[str, float] = {"go": 0.40}


# ── Domain types ──────────────────────────────────────────────────


@dataclass(frozen=True)
class TaskInfo:
    task_id: str
    difficulty_stratum: str
    languages: tuple[str, ...]
    frameworks: tuple[str, ...]


@dataclass(frozen=True)
class Distribution:
    multi_repo_total: int
    ecosystem_counts: dict[str, int]

    def share(self, ecosystem: str) -> float:
        if self.multi_repo_total == 0:
            return 0.0
        return self.ecosystem_counts.get(ecosystem, 0) / self.multi_repo_total


@dataclass(frozen=True)
class Violation:
    ecosystem: str
    cap: float
    before_count: int
    before_share: float
    after_count: int
    after_share: float
    multi_repo_total_before: int
    multi_repo_total_after: int
    reason: str


@dataclass(frozen=True)
class GateResult:
    baseline: Distribution
    candidate: Distribution
    violations: tuple[Violation, ...]
    caps: dict[str, float]

    @property
    def passes(self) -> bool:
        return len(self.violations) == 0


# ── Task loading ──────────────────────────────────────────────────


def _parse_toml(path: Path) -> dict[str, Any]:
    with open(path, "rb") as f:
        return tomllib.load(f)


def _to_task_info(config: dict[str, Any]) -> TaskInfo:
    task_section = config.get("task", {}) or {}
    metadata = config.get("metadata", {}) or {}
    return TaskInfo(
        task_id=task_section.get("id", "unknown"),
        difficulty_stratum=config.get("difficulty_stratum", ""),
        languages=tuple(metadata.get("languages", []) or []),
        frameworks=tuple(metadata.get("frameworks", []) or []),
    )


def load_tasks(benchmarks_dir: Path) -> list[TaskInfo]:
    """Scan a benchmarks directory and return TaskInfo for every active task."""
    if not benchmarks_dir.exists():
        return []
    tasks: list[TaskInfo] = []
    for task_file in sorted(benchmarks_dir.rglob("task.toml")):
        if "_archived" in task_file.parts:
            continue
        try:
            config = _parse_toml(task_file)
        except Exception as exc:  # noqa: BLE001
            # Surface unreadable TOML rather than silently dropping tasks.
            print(
                f"WARN: failed to parse {task_file}: {exc}",
                file=sys.stderr,
            )
            continue
        tasks.append(_to_task_info(config))
    return tasks


def _ecosystem_labels(task: TaskInfo) -> list[str]:
    return [*task.languages, *task.frameworks]


def compute_distribution(tasks: list[TaskInfo]) -> Distribution:
    """Count multi-repo tasks and per-ecosystem occurrences."""
    multi_repo_total = sum(
        1 for t in tasks if t.difficulty_stratum in ALL_MULTI_REPO_STRATA
    )
    counter: Counter[str] = Counter()
    for t in tasks:
        if t.difficulty_stratum in ALL_MULTI_REPO_STRATA:
            for label in _ecosystem_labels(t):
                counter[label] += 1
    return Distribution(
        multi_repo_total=multi_repo_total,
        ecosystem_counts=dict(counter),
    )


# ── Git extraction (for default baseline-from-HEAD mode) ──────────


def _run(cmd: list[str], cwd: Path | None = None) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        cmd,
        cwd=str(cwd) if cwd else None,
        capture_output=True,
        text=True,
        check=False,
    )


def materialize_ref_to_temp(ref: str, repo_root: Path) -> Path | None:
    """Materialize the benchmarks/ tree at `ref` into a temp directory.

    Returns the path to a fresh directory laid out like the benchmarks/
    folder at that ref, or None if the ref does not exist.

    Uses `git ls-tree` + `git cat-file` rather than `git checkout` so the
    working tree is untouched.
    """
    head_check = _run(["git", "rev-parse", "--verify", ref], cwd=repo_root)
    if head_check.returncode != 0:
        return None

    out = _run(
        ["git", "ls-tree", "-r", "--name-only", ref, "benchmarks/"],
        cwd=repo_root,
    )
    if out.returncode != 0:
        return None

    tmp = Path(tempfile.mkdtemp(prefix="ecosystem-gate-baseline-"))
    benchmarks_root = tmp / "benchmarks"
    benchmarks_root.mkdir(parents=True, exist_ok=True)

    for line in out.stdout.splitlines():
        rel = line.strip()
        if not rel.endswith("/task.toml"):
            continue
        # Skip archived per validator convention.
        if "_archived" in rel:
            continue
        blob = _run(["git", "show", f"{ref}:{rel}"], cwd=repo_root)
        if blob.returncode != 0:
            continue
        dest = tmp / rel
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_text(blob.stdout)

    return tmp


# ── Gate evaluation ──────────────────────────────────────────────


def evaluate_gate(
    baseline: Distribution,
    candidate: Distribution,
    caps: dict[str, float],
) -> tuple[Violation, ...]:
    """Identify capped ecosystems whose state worsened past the cap."""
    violations: list[Violation] = []
    for ecosystem, cap in caps.items():
        before_count = baseline.ecosystem_counts.get(ecosystem, 0)
        after_count = candidate.ecosystem_counts.get(ecosystem, 0)
        before_share = baseline.share(ecosystem)
        after_share = candidate.share(ecosystem)

        # Pass: candidate is at or below cap.
        if after_share <= cap:
            continue

        # Past cap. Did the candidate make share worse than baseline?
        # Share, not raw count, is the policy signal: balance-correction
        # commits that add some Go alongside many non-Go tasks legitimately
        # *lower* Go% even when the absolute Go count creeps up, and
        # blocking those would defeat the correction work itself.
        if after_share <= before_share:
            continue

        reason = (
            f"raises ecosystem share ({before_share:.1%} -> {after_share:.1%}) "
            f"while over cap (count {before_count} -> {after_count})"
        )
        violations.append(
            Violation(
                ecosystem=ecosystem,
                cap=cap,
                before_count=before_count,
                before_share=before_share,
                after_count=after_count,
                after_share=after_share,
                multi_repo_total_before=baseline.multi_repo_total,
                multi_repo_total_after=candidate.multi_repo_total,
                reason=reason,
            )
        )
    return tuple(violations)


def run_gate(
    baseline_tasks_dir: Path,
    candidate_tasks_dir: Path,
    caps: dict[str, float],
) -> GateResult:
    baseline_tasks = load_tasks(baseline_tasks_dir)
    candidate_tasks = load_tasks(candidate_tasks_dir)
    baseline_dist = compute_distribution(baseline_tasks)
    candidate_dist = compute_distribution(candidate_tasks)
    violations = evaluate_gate(baseline_dist, candidate_dist, caps)
    return GateResult(
        baseline=baseline_dist,
        candidate=candidate_dist,
        violations=violations,
        caps=dict(caps),
    )


# ── CLI ───────────────────────────────────────────────────────────


def _format_human(result: GateResult) -> str:
    lines: list[str] = []
    lines.append("=" * 70)
    lines.append("EnterpriseBench ecosystem balance gate")
    lines.append("=" * 70)
    lines.append("")
    lines.append(
        f"Baseline multi-repo tasks: {result.baseline.multi_repo_total}"
    )
    lines.append(
        f"Candidate multi-repo tasks: {result.candidate.multi_repo_total}"
    )
    lines.append("")
    lines.append("Capped ecosystems:")
    for eco, cap in sorted(result.caps.items()):
        before = result.baseline.ecosystem_counts.get(eco, 0)
        after = result.candidate.ecosystem_counts.get(eco, 0)
        before_share = result.baseline.share(eco)
        after_share = result.candidate.share(eco)
        marker = " (OVER)" if after_share > cap else ""
        lines.append(
            f"  {eco:12s} cap={cap:.0%}  before={before} ({before_share:.1%})  "
            f"after={after} ({after_share:.1%}){marker}"
        )
    lines.append("")
    if result.passes:
        lines.append("RESULT: PASS — no new ecosystem-cap violations introduced.")
    else:
        lines.append("RESULT: BLOCK — this change worsens a capped ecosystem.")
        lines.append("")
        for v in result.violations:
            lines.append(
                f"  - {v.ecosystem}: {v.reason} (cap={v.cap:.0%}, "
                f"now {v.after_share:.1%}, was {v.before_share:.1%})"
            )
        lines.append("")
        lines.append(
            "Hint: balance the addition with a non-Go task, drop the Go "
            "ecosystem tag if incidental, or land it after the corpus "
            "drops back under cap. See docs/benchmark_design.md."
        )
    return "\n".join(lines)


def _format_json(result: GateResult) -> str:
    payload = {
        "passes": result.passes,
        "caps": result.caps,
        "baseline": {
            "multi_repo_total": result.baseline.multi_repo_total,
            "ecosystem_counts": result.baseline.ecosystem_counts,
        },
        "candidate": {
            "multi_repo_total": result.candidate.multi_repo_total,
            "ecosystem_counts": result.candidate.ecosystem_counts,
        },
        "violations": [
            {
                "ecosystem": v.ecosystem,
                "cap": v.cap,
                "before_count": v.before_count,
                "before_share": round(v.before_share, 4),
                "after_count": v.after_count,
                "after_share": round(v.after_share, 4),
                "reason": v.reason,
            }
            for v in result.violations
        ],
    }
    return json.dumps(payload, indent=2, sort_keys=True)


def _parse_caps(spec: str | None) -> dict[str, float]:
    if not spec:
        return dict(DEFAULT_CAPS)
    caps: dict[str, float] = {}
    for entry in spec.split(","):
        entry = entry.strip()
        if not entry:
            continue
        if "=" not in entry:
            raise ValueError(f"bad cap spec '{entry}', expected eco=fraction")
        eco, frac = entry.split("=", 1)
        caps[eco.strip()] = float(frac.strip())
    return caps


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Block commits that worsen a capped ecosystem on multi-repo tasks."
        ),
    )
    parser.add_argument(
        "--baseline-ref",
        default="HEAD",
        help="Git ref to compare against when --baseline-tasks-dir is unset.",
    )
    parser.add_argument(
        "--baseline-tasks-dir",
        type=Path,
        default=None,
        help=(
            "Optional path to a benchmarks/ tree to use as baseline. "
            "Overrides --baseline-ref."
        ),
    )
    parser.add_argument(
        "--candidate-tasks-dir",
        type=Path,
        default=None,
        help=(
            "Optional path to a benchmarks/ tree to use as candidate. "
            "Defaults to working tree at $REPO_ROOT/benchmarks/."
        ),
    )
    parser.add_argument(
        "--repo-root",
        type=Path,
        default=PROJECT_ROOT,
        help="Repository root (defaults to project root).",
    )
    parser.add_argument(
        "--caps",
        default=None,
        help=(
            "Comma-separated overrides, e.g. 'go=0.40,python=0.30'. "
            "Default: go=0.40."
        ),
    )
    parser.add_argument(
        "--json", action="store_true", help="Output JSON instead of human text."
    )
    args = parser.parse_args(argv)

    try:
        caps = _parse_caps(args.caps)
    except ValueError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2

    repo_root: Path = args.repo_root.resolve()

    baseline_dir: Path | None
    cleanup_baseline: Path | None = None
    if args.baseline_tasks_dir is not None:
        baseline_dir = args.baseline_tasks_dir
    else:
        materialized = materialize_ref_to_temp(args.baseline_ref, repo_root)
        if materialized is None:
            print(
                f"error: cannot resolve baseline ref '{args.baseline_ref}'",
                file=sys.stderr,
            )
            return 2
        baseline_dir = materialized / "benchmarks"
        cleanup_baseline = materialized

    candidate_dir = (
        args.candidate_tasks_dir
        if args.candidate_tasks_dir is not None
        else repo_root / "benchmarks"
    )

    try:
        result = run_gate(baseline_dir, candidate_dir, caps)
    finally:
        if cleanup_baseline is not None:
            shutil.rmtree(cleanup_baseline, ignore_errors=True)

    if args.json:
        print(_format_json(result))
    else:
        print(_format_human(result))

    return 0 if result.passes else 1


if __name__ == "__main__":
    sys.exit(main())
