#!/usr/bin/env python3
"""Unified task dispatcher CLI for EnterpriseBench.

Routes tasks to session-type-specific runners and collects results.

Usage:
    # Single task
    python3 scripts/run_benchmark.py benchmarks/customer_escalation/err-provenance-01/task.toml

    # All tasks in a suite
    python3 scripts/run_benchmark.py benchmarks/customer_escalation/

    # All tasks with filters
    python3 scripts/run_benchmark.py benchmarks/ --all --difficulty medium --limit 5

    # Parallel across 5 accounts (auto workers = one per account)
    python3 scripts/run_benchmark.py benchmarks/ --all --account 1-5 -j0

    # 3 parallel workers on accounts 1,3,5
    python3 scripts/run_benchmark.py benchmarks/ --all --account 1,3,5 -j3

    # Dry run (list matching tasks without executing)
    python3 scripts/run_benchmark.py benchmarks/ --all --dry-run
"""

from __future__ import annotations

import argparse
import json
import logging
import subprocess
import sys
import time
from concurrent.futures import ProcessPoolExecutor, as_completed
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Sequence

# Support both tomllib (3.11+) and tomli
try:
    import tomllib
except ImportError:
    try:
        import tomli as tomllib  # type: ignore[no-redefine]
    except ImportError:
        tomllib = None  # type: ignore[assignment]

logger = logging.getLogger("run_benchmark")

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from agents.harnesses.registry import (  # noqa: E402
    CODEX_REASONING_EFFORTS,
    HARNESS_NAMES,
    HarnessConfigurationError,
    build_harness_plan,
    harness_variant_label,
)

sys.path.insert(0, str(PROJECT_ROOT / "scripts" / "infra"))
from capacity import retired_accounts  # noqa: E402

# Session-type to runner script mapping
RUNNERS: dict[str, Path] = {
    "single": PROJECT_ROOT / "scripts" / "orchestration" / "run_task.py",
    "chain": PROJECT_ROOT / "scripts" / "orchestration" / "chain_runner.py",
    "event_replay": PROJECT_ROOT / "scripts" / "orchestration" / "event_replay.py",
}


# ---------------------------------------------------------------------------
# Data
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class TaskInfo:
    """Lightweight metadata parsed from a task.toml for filtering/routing."""

    task_id: str
    suite: str
    difficulty: str
    session_type: str
    task_type: str
    toml_path: Path


VALID_MODES = (
    "baseline",
    "mcp_only",
    "mcp_default",
    "mcp_treatment",
    "mcp_code_finder",
    "mcp_assisted",
    "mcp_toast",
    "hybrid",
    "cli",
)


@dataclass
class TaskResult:
    task_id: str
    difficulty: str
    score: float | None = None
    duration_seconds: float = 0.0
    status: str = "pending"
    mode: str = "baseline"


@dataclass(frozen=True)
class TokenBudget:
    """Immutable token/cost budget tracker.

    Checks cumulative cost against a budget limit and a warning threshold.
    When budget_usd is None, all checks return False (unlimited).
    """

    budget_usd: float | None
    warn_pct: int = 80

    def is_exceeded(self, cumulative_cost: float) -> bool:
        """Return True if cumulative cost meets or exceeds the budget."""
        if self.budget_usd is None:
            return False
        return cumulative_cost >= self.budget_usd

    def should_warn(self, cumulative_cost: float) -> bool:
        """Return True if cumulative cost is at or above the warning threshold."""
        if self.budget_usd is None:
            return False
        threshold = self.budget_usd * (self.warn_pct / 100.0)
        return cumulative_cost >= threshold

    def remaining_usd(self, cumulative_cost: float) -> float:
        """Return remaining budget in USD, or inf if no budget."""
        if self.budget_usd is None:
            return float("inf")
        return self.budget_usd - cumulative_cost

    def pct_used(self, cumulative_cost: float) -> float:
        """Return percentage of budget consumed, or 0.0 if no budget."""
        if self.budget_usd is None:
            return 0.0
        return (cumulative_cost / self.budget_usd) * 100.0


# ---------------------------------------------------------------------------
# Discovery & Filtering
# ---------------------------------------------------------------------------


def discover_tasks(path: Path) -> list[TaskInfo]:
    """Find all task.toml files under *path* and parse their metadata."""
    if path.is_file() and path.name == "task.toml":
        toml_paths = [path]
    elif path.is_dir():
        toml_paths = sorted(path.rglob("task.toml"))
    else:
        logger.error("Path is neither a task.toml file nor a directory: %s", path)
        return []

    tasks: list[TaskInfo] = []
    for tp in toml_paths:
        info = _parse_task_meta(tp)
        if info is not None:
            tasks.append(info)
    return tasks


def _parse_task_meta(toml_path: Path) -> TaskInfo | None:
    if tomllib is None:
        logger.error("No TOML library available. Install tomli or use Python 3.11+.")
        sys.exit(1)

    try:
        with open(toml_path, "rb") as f:
            data = tomllib.load(f)
    except Exception as exc:
        logger.warning("Failed to parse %s: %s", toml_path, exc)
        return None

    task = data.get("task", {})
    return TaskInfo(
        task_id=task.get("id", toml_path.parent.name),
        suite=task.get("suite", ""),
        difficulty=task.get("difficulty", ""),
        session_type=task.get("session_type", "single"),
        task_type=task.get("task_type", ""),
        toml_path=toml_path.resolve(),
    )


def manifest_task_ids(manifest_path: Path) -> set[str]:
    """Task ids named by a slice manifest.

    Manifests list tasks suite-qualified ("suite/task-id") because bare ids
    are not unique -- calibration-002 exists in three suites. Dispatch keys
    off the bare id, so the suite prefix is stripped here; the manifest's own
    gate (scripts/audit_disclosure.py) is what validates the qualified form.
    """
    manifest = json.loads(manifest_path.read_text())
    return {entry.rsplit("/", 1)[-1] for entry in manifest["tasks"]}


def filter_tasks(
    tasks: list[TaskInfo],
    *,
    difficulty: str | None = None,
    session_type: str | None = None,
    task_type: str | None = None,
    task_ids: set[str] | None = None,
    limit: int | None = None,
) -> list[TaskInfo]:
    """Return a new list filtered by the given criteria."""
    result = list(tasks)
    if task_ids is not None:
        result = [t for t in result if t.task_id in task_ids]
    if difficulty:
        result = [t for t in result if t.difficulty == difficulty]
    if session_type:
        result = [t for t in result if t.session_type == session_type]
    if task_type:
        result = [t for t in result if t.task_type == task_type]
    if limit is not None and limit > 0:
        result = result[:limit]
    return result


def is_task_completed(
    task_id: str,
    *,
    results_dir: Path | None = None,
    mode: str = "baseline",
    variant_label: str | None = None,
) -> bool:
    """Check whether a task already has a successful results.json.

    Looks in two locations (in order):
      1. ``results_dir/<task_id>/<mode>/results.json`` (multi-mode layout)
      2. ``results_dir/<task_id>/results.json``        (single-mode layout, baseline only)

    Returns True only if the file exists, parses as JSON, and contains
    ``"success": true``.
    """
    if results_dir is None:
        results_dir = PROJECT_ROOT / "results" / "runs"

    segment = f"{mode}--{variant_label}" if variant_label else mode
    candidates = [results_dir / task_id / segment / "results.json"]
    # Legacy single-mode layout only matches baseline
    if mode == "baseline" and variant_label is None:
        candidates.append(results_dir / task_id / "results.json")
    for path in candidates:
        if not path.exists():
            continue
        try:
            data = json.loads(path.read_text())
            if data.get("success") is True:
                return True
        except (json.JSONDecodeError, OSError):
            continue
    return False


def filter_completed_tasks(
    tasks: list[TaskInfo],
    *,
    results_dir: Path | None = None,
    mode: str = "baseline",
    variant_label: str | None = None,
) -> tuple[list[TaskInfo], list[TaskInfo]]:
    """Partition *tasks* into (remaining, skipped) based on existing results.

    A task is considered completed (and therefore skipped) when
    ``is_task_completed`` returns True for its task_id.
    """
    remaining: list[TaskInfo] = []
    skipped: list[TaskInfo] = []
    for t in tasks:
        if is_task_completed(
            t.task_id,
            results_dir=results_dir,
            mode=mode,
            variant_label=variant_label,
        ):
            skipped.append(t)
        else:
            remaining.append(t)
    return remaining, skipped


# ---------------------------------------------------------------------------
# Cost Extraction
# ---------------------------------------------------------------------------


def extract_task_cost(
    task_id: str,
    *,
    results_dir: Path | None = None,
    mode: str = "baseline",
    variant_label: str | None = None,
) -> float:
    """Read cost_usd from a task's results.json tool_usage section.

    Checks mode-specific and legacy results locations.
    Returns 0.0 if the file is missing or has no cost data.
    """
    if results_dir is None:
        results_dir = PROJECT_ROOT / "results" / "runs"

    segment = f"{mode}--{variant_label}" if variant_label else mode
    candidates = [results_dir / task_id / segment / "results.json"]
    if mode == "baseline" and variant_label is None:
        candidates.append(results_dir / task_id / "results.json")
    for path in candidates:
        if not path.exists():
            continue
        try:
            data = json.loads(path.read_text())
            tool_usage = data.get("tool_usage", {})
            cost = tool_usage.get("cost_usd", 0.0)
            if cost:
                return float(cost)
        except (json.JSONDecodeError, OSError, TypeError, ValueError):
            continue
    return 0.0


# ---------------------------------------------------------------------------
# Execution
# ---------------------------------------------------------------------------


def _passthrough_value(args: Sequence[str], flag: str) -> str | None:
    """Return the value following *flag* in a generated passthrough argv."""
    try:
        index = args.index(flag)
    except ValueError:
        return None
    return args[index + 1] if index + 1 < len(args) else None


def run_task(
    task: TaskInfo,
    *,
    passthrough_args: Sequence[str],
    dry_run: bool = False,
    mode: str = "baseline",
) -> TaskResult:
    """Dispatch a single task to the appropriate runner."""
    result = TaskResult(task_id=task.task_id, difficulty=task.difficulty, mode=mode)

    if task.session_type == "resume":
        logger.info(
            "[skip] %s — session_type 'resume' not yet implemented", task.task_id
        )
        result.status = "skipped"
        return result

    runner = RUNNERS.get(task.session_type)
    if runner is None:
        logger.error(
            "[skip] %s — unknown session_type '%s'", task.task_id, task.session_type
        )
        result.status = "skipped"
        return result

    cmd = [sys.executable, str(runner), str(task.toml_path), *passthrough_args]

    if dry_run:
        logger.info("[dry-run] would run: %s", " ".join(cmd))
        result.status = "dry-run"
        return result

    logger.info("[run] %s (session_type=%s)", task.task_id, task.session_type)
    t0 = time.monotonic()
    try:
        proc = subprocess.run(cmd, capture_output=True, text=True, timeout=3600)
        result.duration_seconds = time.monotonic() - t0

        if proc.returncode != 0:
            logger.warning(
                "[error] %s exited %d\nstdout: %s\nstderr: %s",
                task.task_id,
                proc.returncode,
                proc.stdout[-500:] if proc.stdout else "",
                proc.stderr[-500:] if proc.stderr else "",
            )
            result.status = "error"
            return result

        output_override = _passthrough_value(passthrough_args, "--output-dir")
        variant_label = _passthrough_value(passthrough_args, "--variant-label")
        segment = f"{mode}--{variant_label}" if variant_label else mode
        generated_path = (
            Path(output_override) / "results.json"
            if output_override
            else PROJECT_ROOT
            / "results"
            / "runs"
            / task.task_id
            / segment
            / "results.json"
        )

        # Labeled harness runs have exactly one result identity. Legacy
        # fallbacks are valid only for unlabeled runs; otherwise an old Claude
        # baseline can be mistaken for a newly completed Codex/OpenCode run.
        result_candidates = [generated_path]
        if variant_label is None:
            result_candidates.extend(
                [
                    PROJECT_ROOT
                    / "results"
                    / "runs"
                    / task.task_id
                    / "results.json",
                    task.toml_path.parent / "results.json",
                ]
            )
        for results_file in result_candidates:
            if results_file.exists():
                try:
                    rdata = json.loads(results_file.read_text())
                    result.score = rdata.get("scores", {}).get(
                        "task_score", rdata.get("score")
                    )
                    break
                except (json.JSONDecodeError, OSError):
                    pass

        result.status = "completed"

    except subprocess.TimeoutExpired:
        result.duration_seconds = time.monotonic() - t0
        result.status = "timeout"
        logger.warning("[timeout] %s", task.task_id)
    except Exception as exc:
        result.duration_seconds = time.monotonic() - t0
        result.status = "error"
        logger.error("[error] %s — %s", task.task_id, exc)

    return result


# ---------------------------------------------------------------------------
# Parallel worker (module-level for pickling)
# ---------------------------------------------------------------------------


def _mode_output_dir(
    task_id: str,
    mode: str,
    *,
    multi_mode: bool,
    variant_label: str | None = None,
) -> Path | None:
    """Per-mode results directory, or None for single-mode runs.

    Multi-mode sweeps route each mode to its own subdirectory so results from
    different modes do not overwrite each other; a variant label extends the
    segment (<mode>--<label>) so tuning runs do not overwrite production runs
    either. A single-mode run uses the runner's default output path (which
    applies the same label segment itself). Only run_task (single-session tasks)
    honors the forwarded --output-dir; chain_runner and event_replay accept it
    but write to their own fixed paths, so their multi-mode outputs still
    collide — tracked separately, out of scope for the argparse contract this
    file establishes.
    """
    if not multi_mode:
        return None
    segment = f"{mode}--{variant_label}" if variant_label else mode
    return PROJECT_ROOT / "results" / "runs" / task_id / segment


def _effective_variant_label(args: argparse.Namespace) -> str | None:
    """Resolve an explicit run label or isolate a generated harness/model arm."""
    explicit = getattr(args, "variant_label", None)
    if explicit:
        return explicit
    return harness_variant_label(
        getattr(args, "harness", "claude"),
        getattr(args, "model", None),
    )


def _run_task_with_account(
    task: TaskInfo,
    account_id: int | None,
    mode: str,
    cli_args: argparse.Namespace,
    multi_mode: bool,
) -> TaskResult:
    """Run a single task with account assignment. Module-level for ProcessPoolExecutor."""
    pt = collect_passthrough_args(
        cli_args,
        account_override=account_id,
        mode_override=mode,
        output_dir=_mode_output_dir(
            task.task_id,
            mode,
            multi_mode=multi_mode,
            variant_label=_effective_variant_label(cli_args),
        ),
    )
    return run_task(task, passthrough_args=pt, mode=mode)


# ---------------------------------------------------------------------------
# Summary
# ---------------------------------------------------------------------------


def write_summary(
    results: list[TaskResult],
    run_id: str,
    previously_completed: int = 0,
    cumulative_cost_usd: float = 0.0,
    budget_usd: float | None = None,
) -> Path:
    """Write results/runs/<run_id>/summary.json and return the path."""
    out_dir = PROJECT_ROOT / "results" / "runs" / run_id
    out_dir.mkdir(parents=True, exist_ok=True)
    summary_path = out_dir / "summary.json"

    payload: dict = {
        "run_id": run_id,
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "total": len(results),
        "completed": sum(1 for r in results if r.status == "completed"),
        "errors": sum(1 for r in results if r.status == "error"),
        "skipped": sum(1 for r in results if r.status == "skipped"),
        "previously_completed": previously_completed,
        "cumulative_cost_usd": cumulative_cost_usd,
    }
    if budget_usd is not None:
        payload["budget_usd"] = budget_usd
    payload["tasks"] = [asdict(r) for r in results]
    summary_path.write_text(json.dumps(payload, indent=2) + "\n")
    return summary_path


def print_summary_table(results: list[TaskResult]) -> None:
    """Print a human-readable summary table to stdout."""
    header = f"{'task_id':<45} {'mode':<12} {'difficulty':<10} {'score':>6} {'duration':>10} {'status':<10}"
    print("\n" + header)
    print("-" * len(header))
    for r in results:
        score_str = f"{r.score:.2f}" if r.score is not None else "—"
        dur_str = f"{r.duration_seconds:.1f}s" if r.duration_seconds > 0 else "—"
        print(
            f"{r.task_id:<45} {r.mode:<12} {r.difficulty:<10} {score_str:>6} {dur_str:>10} {r.status:<10}"
        )


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="EnterpriseBench unified task dispatcher",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    parser.add_argument(
        "path",
        type=Path,
        help="Path to a task.toml file or a directory containing tasks",
    )

    # Filter flags
    filter_group = parser.add_argument_group("filters")
    filter_group.add_argument(
        "--all", action="store_true", help="Run all tasks under path"
    )
    filter_group.add_argument("--difficulty", choices=["medium", "hard", "expert"])
    filter_group.add_argument(
        "--session-type", choices=["single", "chain", "event_replay", "resume"]
    )
    filter_group.add_argument("--task-type")
    filter_group.add_argument(
        "--manifest",
        type=Path,
        default=None,
        help=(
            "Slice manifest ({'tasks': ['suite/task-id', ...]}) restricting the "
            "run to the tasks it names, e.g. configs/retrieval_stress_set.json"
        ),
    )
    filter_group.add_argument(
        "--limit", type=int, default=None, help="Max number of tasks to run"
    )
    filter_group.add_argument(
        "--skip-completed",
        action="store_true",
        default=False,
        help=(
            "Skip tasks whose results/runs/<task_id>/results.json "
            "already contains success=true"
        ),
    )

    # Passthrough flags for runners
    runner_group = parser.add_argument_group("runner options (passed through)")
    runner_group.add_argument("--source", choices=["mirror", "upstream"])
    runner_group.add_argument("--agent", type=str)
    runner_group.add_argument(
        "--harness",
        choices=list(HARNESS_NAMES),
        default="claude",
        help="Agent harness to run (default: claude)",
    )
    runner_group.add_argument(
        "--reasoning-effort",
        choices=list(CODEX_REASONING_EFFORTS),
        default=None,
        help="Codex reasoning effort (codex harness only).",
    )
    runner_group.add_argument(
        "--model",
        default=None,
        help=(
            "Explicit model ID. Required for codex and opencode; OpenCode "
            "expects provider/model, for example "
            "openrouter/deepseek/deepseek-v4-pro."
        ),
    )
    runner_group.add_argument("--timeout", type=int)
    runner_group.add_argument(
        "--account",
        type=str,
        default=None,
        help=(
            "OAuth account(s): single number (1), range (1-5), or "
            "comma-separated (1,3,5). Tasks are distributed round-robin "
            "across accounts when multiple are given."
        ),
    )
    runner_group.add_argument(
        "--judge-model",
        default="cc:haiku",
        help="Tier-2 judge model/backend identifier (default: cc:haiku).",
    )
    runner_group.add_argument(
        "--judge-account",
        type=int,
        default=None,
        help=(
            "Claude account wrapper used only by a cc:* judge "
            "(for example 1 selects claude-1)."
        ),
    )
    runner_group.add_argument(
        "--dry-run", action="store_true", help="List tasks without running"
    )
    runner_group.add_argument(
        "--mode",
        choices=list(VALID_MODES),
        default="baseline",
        help="Tool-access mode (default: baseline)",
    )
    runner_group.add_argument(
        "--modes",
        type=str,
        default=None,
        help=(
            "Comma-separated list of modes to run each task in "
            "(e.g. 'baseline,mcp_code_finder,mcp_assisted'). Overrides --mode."
        ),
    )
    runner_group.add_argument(
        "--variant-label",
        type=str,
        default=None,
        help=(
            "Display label for a prompt/preamble tuning iteration. Forwarded "
            "to the runner; labeled runs are routed to "
            "results/runs/<task_id>/<mode>--<label>/ and never overwrite or "
            "satisfy completion checks for unlabeled (production) runs."
        ),
    )

    # Parallelism
    parallel_group = parser.add_argument_group("parallelism")
    parallel_group.add_argument(
        "--parallel",
        "-j",
        type=int,
        default=1,
        metavar="N",
        help="Max parallel tasks (default: 1). Set to 0 for auto (one per account).",
    )

    # Budget watchdog
    budget_group = parser.add_argument_group("budget watchdog")
    budget_group.add_argument(
        "--budget-usd",
        type=float,
        default=None,
        help="Maximum cumulative cost in USD. Stops execution when exceeded.",
    )
    budget_group.add_argument(
        "--budget-warn-pct",
        type=int,
        default=80,
        metavar="PCT",
        help="Warn when cumulative cost reaches this %% of budget (default: 80).",
    )

    parser.add_argument("-v", "--verbose", action="store_true")
    return parser


def parse_accounts(account_str: str | None) -> list[int]:
    """Parse account spec into a list of account numbers, minus retired ones.

    Supports: "1", "1-5", "1,3,5", "1-3,5".
    Returns an empty list if account_str is None.

    Retired accounts are dropped here rather than rejected, because "1-5" is
    the range in every runbook and habit, and failing the whole sweep on it
    helps nobody. Dropping is safe precisely because a retired account cannot
    produce a score: its runs die in under ten seconds on an HTTP 403 and
    route to ``verifier_infra_error``. Silently keeping it in the round-robin
    is the harmful option -- it costs every Nth task in the sweep and the
    losses look like task failures.
    """
    if not account_str:
        return []
    accounts: list[int] = []
    for part in account_str.split(","):
        part = part.strip()
        if "-" in part:
            lo, hi = part.split("-", 1)
            accounts.extend(range(int(lo), int(hi) + 1))
        else:
            accounts.append(int(part))

    retired = {
        int(name.removeprefix("account"))
        for name in retired_accounts()
        if name.removeprefix("account").isdigit()
    }
    live = [account for account in accounts if account not in retired]
    dropped = sorted(set(accounts) - set(live))
    if dropped:
        logger.warning(
            "Skipping retired account(s) %s; dispatching to %s",
            ", ".join(str(account) for account in dropped),
            ", ".join(str(account) for account in live) or "no accounts",
        )
    if accounts and not live:
        raise SystemExit(
            f"every account in --account {account_str} is retired; "
            "nothing can be dispatched"
        )
    return live


def collect_passthrough_args(
    args: argparse.Namespace,
    *,
    account_override: int | None = None,
    mode_override: str | None = None,
    output_dir: Path | None = None,
) -> list[str]:
    """Build list of flags to pass through to underlying runner scripts.

    If *account_override* is given it replaces whatever --account was on the CLI
    (used by the parallel dispatcher to rotate accounts per task).
    If *mode_override* is given it replaces the --mode flag.
    If *output_dir* is given a ``--output-dir`` flag is appended (used for
    multi-mode runs that route each mode to its own subdirectory). This function
    is the sole *emitter* of the forwarded flags — callers must not append flags
    afterward, or the runner-contract test (which pins the emitted set against
    runner_cli.PASSTHROUGH_FLAGS) cannot guarantee every runner accepts them
    (EnterpriseBench-nw70h).
    """
    result: list[str] = []
    if args.source:
        result.extend(["--source", args.source])
    if args.agent:
        result.extend(["--agent", args.agent])
    harness = getattr(args, "harness", "claude")
    model = getattr(args, "model", None)
    result.extend(["--harness", harness])
    if model:
        result.extend(["--model", model])
    effort = getattr(args, "reasoning_effort", None)
    if effort:
        result.extend(["--reasoning-effort", effort])
    if args.timeout:
        result.extend(["--timeout", str(args.timeout)])
    if account_override is not None:
        result.extend(["--account", str(account_override)])
    elif args.account is not None:
        # Single-account legacy path
        accounts = parse_accounts(args.account)
        if len(accounts) == 1:
            result.extend(["--account", str(accounts[0])])
    judge_model = getattr(args, "judge_model", None)
    if judge_model:
        result.extend(["--judge-model", judge_model])
    judge_account = getattr(args, "judge_account", None)
    if judge_account is not None:
        result.extend(["--judge-account", str(judge_account)])
    # Mode passthrough
    mode = mode_override if mode_override is not None else args.mode
    result.extend(["--mode", mode])
    if args.dry_run:
        result.append("--dry-run")
    if output_dir is not None:
        result.extend(["--output-dir", str(output_dir)])
    variant_label = _effective_variant_label(args)
    if variant_label:
        result.extend(["--variant-label", variant_label])
    return result


def main(argv: Sequence[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)

    if args.harness != "claude" and args.account is not None:
        parser.error("--account selects Claude OAuth and cannot be used with --harness")
    if args.harness != "claude" and args.agent:
        parser.error("--agent cannot be combined with generated codex/opencode harnesses")
    try:
        build_harness_plan(
            args.harness,
            model=args.model,
            mode=args.mode,
            agent_command=args.agent or "",
        )
    except HarnessConfigurationError as exc:
        parser.error(str(exc))

    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(levelname)s %(message)s",
    )

    path = args.path.resolve()

    # Require --all when pointing at a directory (safety guard)
    if path.is_dir() and not path.name.endswith("task.toml") and not args.all:
        # If pointing at a single task dir (contains task.toml), allow it
        if (path / "task.toml").exists():
            path = path / "task.toml"
        else:
            parser.error("Pass --all to run all tasks in a directory")

    manifest_ids = manifest_task_ids(args.manifest) if args.manifest else None

    tasks = discover_tasks(path)
    if not tasks:
        logger.error("No tasks found at %s", path)
        return 1

    tasks = filter_tasks(
        tasks,
        difficulty=args.difficulty,
        session_type=args.session_type,
        task_type=args.task_type,
        task_ids=manifest_ids,
        limit=args.limit,
    )

    if args.harness != "claude":
        unsupported = sorted(
            task.task_id for task in tasks if task.session_type != "single"
        )
        if unsupported:
            parser.error(
                f"harness={args.harness} currently supports session_type=single; "
                f"unsupported task(s): {', '.join(unsupported[:5])}"
            )

    logger.info("Matched %d task(s)", len(tasks))

    # --skip-completed: remove tasks that already have successful results
    previously_completed = 0
    if args.skip_completed:
        results_dir = PROJECT_ROOT / "results" / "runs"
        # Determine the mode(s) to check against
        check_mode = args.mode  # default; multi-mode handled per-mode below
        tasks, skipped_tasks = filter_completed_tasks(
            tasks,
            results_dir=results_dir,
            mode=check_mode,
            variant_label=_effective_variant_label(args),
        )
        previously_completed = len(skipped_tasks)
        if previously_completed > 0:
            for st in skipped_tasks:
                logger.info("[skip-completed] %s", st.task_id)
            logger.info(
                "Skipped %d previously completed task(s), %d remaining",
                previously_completed,
                len(tasks),
            )

    if args.limit == 0:
        # --limit 0 means list only, run nothing
        for t in discover_tasks(path):
            filtered = filter_tasks(
                [t],
                difficulty=args.difficulty,
                session_type=args.session_type,
                task_type=args.task_type,
                task_ids=manifest_ids,
            )
            if filtered:
                print(
                    f"  {t.task_id:<45} {t.difficulty:<10} {t.session_type:<15} {t.task_type}"
                )
        return 0

    if not tasks:
        logger.info("No tasks match the given filters.")
        return 0

    # Resolve modes list
    if args.modes:
        modes = [m.strip() for m in args.modes.split(",")]
        for m in modes:
            if m not in VALID_MODES:
                parser.error(
                    f"Invalid mode '{m}' in --modes. "
                    f"Valid modes: {', '.join(VALID_MODES)}"
                )
    else:
        modes = [args.mode]

    for selected_mode in modes:
        try:
            build_harness_plan(
                args.harness,
                model=args.model,
                mode=selected_mode,
                agent_command=args.agent or "",
            )
        except HarnessConfigurationError as exc:
            parser.error(str(exc))

    logger.info("Tool-access mode(s): %s", ", ".join(modes))

    # Execute
    accounts = parse_accounts(args.account)
    workers = args.parallel
    if workers == 0:
        # Auto: one worker per account, minimum 1
        workers = max(len(accounts), 1)

    run_id = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
    results: list[TaskResult] = []

    # Token budget watchdog
    budget = TokenBudget(
        budget_usd=args.budget_usd,
        warn_pct=args.budget_warn_pct,
    )
    cumulative_cost_usd: float = 0.0
    budget_exceeded = False
    budget_warned = False

    if budget.budget_usd is not None:
        logger.info(
            "Budget watchdog: $%.2f limit, warn at %d%%",
            budget.budget_usd,
            budget.warn_pct,
        )

    results_dir = PROJECT_ROOT / "results" / "runs"

    for current_mode in modes:
        if budget_exceeded:
            break

        if len(modes) > 1:
            logger.info("--- Running mode: %s ---", current_mode)

        if workers <= 1 or args.dry_run:
            # Sequential execution
            for task in tasks:
                # Check budget before starting each task
                if budget.is_exceeded(cumulative_cost_usd):
                    logger.warning(
                        "[budget] STOPPED — cumulative cost $%.2f exceeds "
                        "budget $%.2f. Remaining tasks skipped.",
                        cumulative_cost_usd,
                        budget.budget_usd,
                    )
                    budget_exceeded = True
                    break

                account_id = accounts[0] if len(accounts) == 1 else None
                passthrough = collect_passthrough_args(
                    args,
                    account_override=account_id,
                    mode_override=current_mode,
                    output_dir=_mode_output_dir(
                        task.task_id,
                        current_mode,
                        multi_mode=len(modes) > 1,
                        variant_label=_effective_variant_label(args),
                    ),
                )
                result = run_task(
                    task,
                    passthrough_args=passthrough,
                    dry_run=args.dry_run,
                    mode=current_mode,
                )
                results.append(result)

                # Track cost after task completion
                if not args.dry_run:
                    task_cost = extract_task_cost(
                        task.task_id,
                        results_dir=results_dir,
                        mode=current_mode,
                        variant_label=_effective_variant_label(args),
                    )
                    cumulative_cost_usd += task_cost

                    if not budget_warned and budget.should_warn(cumulative_cost_usd):
                        logger.warning(
                            "[budget] WARNING — cumulative cost $%.2f has "
                            "reached %.0f%% of $%.2f budget",
                            cumulative_cost_usd,
                            budget.pct_used(cumulative_cost_usd),
                            budget.budget_usd,
                        )
                        budget_warned = True
        else:
            # Parallel execution with round-robin account distribution
            logger.info(
                "Parallel mode: %d workers, %d account(s)",
                workers,
                len(accounts) or 1,
            )

            multi_mode = len(modes) > 1

            # Build (task, account) pairs via round-robin
            task_assignments: list[tuple[TaskInfo, int | None]] = []
            for i, task in enumerate(tasks):
                acct = accounts[i % len(accounts)] if accounts else None
                task_assignments.append((task, acct))

            # Submit all tasks to the pool
            # Use (task_id, mode) as key to handle multi-mode runs
            result_map: dict[str, TaskResult] = {}
            with ProcessPoolExecutor(max_workers=workers) as pool:
                future_to_task = {
                    pool.submit(
                        _run_task_with_account,
                        task,
                        acct,
                        current_mode,
                        args,
                        multi_mode,
                    ): task
                    for task, acct in task_assignments
                }
                for future in as_completed(future_to_task):
                    task = future_to_task[future]
                    try:
                        r = future.result()
                    except Exception as exc:
                        r = TaskResult(
                            task_id=task.task_id,
                            difficulty=task.difficulty,
                            status="error",
                            mode=current_mode,
                        )
                        logger.error("[error] %s — %s", task.task_id, exc)
                    result_map[task.task_id] = r

                    # Track cost from completed parallel task
                    task_cost = extract_task_cost(
                        task.task_id,
                        results_dir=results_dir,
                        mode=current_mode,
                        variant_label=_effective_variant_label(args),
                    )
                    cumulative_cost_usd += task_cost

                    logger.info(
                        "[done] %s [%s]  score=%s  %s  (%.0fs)  cost=$%.2f  cumulative=$%.2f",
                        r.task_id,
                        r.mode,
                        f"{r.score:.2f}" if r.score is not None else "—",
                        r.status,
                        r.duration_seconds,
                        task_cost,
                        cumulative_cost_usd,
                    )

                    if not budget_warned and budget.should_warn(cumulative_cost_usd):
                        logger.warning(
                            "[budget] WARNING — cumulative cost $%.2f has "
                            "reached %.0f%% of $%.2f budget",
                            cumulative_cost_usd,
                            budget.pct_used(cumulative_cost_usd),
                            budget.budget_usd,
                        )
                        budget_warned = True

                    # Note: we cannot cancel in-flight futures, but we can
                    # stop picking up new results processing. The pool will
                    # finish existing tasks but no new ones are submitted.
                    if budget.is_exceeded(cumulative_cost_usd):
                        logger.warning(
                            "[budget] STOPPED — cumulative cost $%.2f exceeds "
                            "budget $%.2f. In-flight tasks will finish.",
                            cumulative_cost_usd,
                            budget.budget_usd,
                        )
                        budget_exceeded = True

            # Preserve original task order in results
            results.extend(
                result_map[t.task_id] for t in tasks if t.task_id in result_map
            )

    # Summary
    print_summary_table(results)
    if not args.dry_run:
        summary_path = write_summary(
            results,
            run_id,
            previously_completed=previously_completed,
            cumulative_cost_usd=cumulative_cost_usd,
            budget_usd=budget.budget_usd,
        )
        logger.info("Summary written to %s", summary_path)

    if budget_exceeded:
        logger.warning(
            "[budget] Run terminated early. Cumulative cost: $%.2f / $%.2f",
            cumulative_cost_usd,
            budget.budget_usd,
        )
        return 3

    return 0


if __name__ == "__main__":
    sys.exit(main())
