#!/usr/bin/env python3
"""Three-mode sweep runner for EnterpriseBench.

Orchestrates running all tasks in 3 modes (baseline / mcp_only / hybrid).
Skips already-scored task+mode combos. Generates a sweep manifest listing
all task+mode pairs with status. Parallelizes across accounts 1-5.

Usage:
    # Generate manifest only
    python3 scripts/run_sweep.py --manifest-only

    # Generate manifest and print run commands
    python3 scripts/run_sweep.py --modes baseline,mcp_only

    # Generate manifest, print commands, and execute them
    python3 scripts/run_sweep.py --execute --account 1-5

    # Custom results dirs
    python3 scripts/run_sweep.py --results-dir results/runs --results-dir results/mcp_batch

    # Publish sweep: baseline/mcp_only/cli (hybrid cut), pinned model. The
    # default matrix is still the old 3-mode one — a publish run MUST pass
    # --matrix publish explicitly, it is never inferred.
    python3 scripts/run_sweep.py --matrix publish --model claude-sonnet-5 \\
        --execute --account 1-5
"""

from __future__ import annotations

import argparse
import json
import logging
import subprocess
import sys
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path

# ---------------------------------------------------------------------------
# Imports from run_benchmark
# ---------------------------------------------------------------------------

_SCRIPTS_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(_SCRIPTS_DIR))

from run_benchmark import TaskInfo, discover_tasks  # noqa: E402

logger = logging.getLogger("run_sweep")

PROJECT_ROOT = _SCRIPTS_DIR.parent

ALL_MODES = ("baseline", "mcp_only", "hybrid", "cli")

# Modes an UNSCOPED sweep runs by default. `cli` is a real, valid mode (it is in
# ALL_MODES and accepted by --modes), but it is deliberately excluded from the
# default so an existing sweep does not silently gain a 4th arm — inflating cost
# ~33% — the day the cli port lands. Request it explicitly: --modes ... cli.
DEFAULT_SWEEP_MODES = ("baseline", "mcp_only", "hybrid")

# Named mode-matrix presets, selected via --matrix instead of --modes. Added
# rather than changing DEFAULT_SWEEP_MODES itself so an existing sweep script
# or cron job that omits --modes/--matrix keeps running the old 3-mode matrix —
# a publish sweep must opt in explicitly with `--matrix publish`.
MATRIX_PRESETS: dict[str, tuple[str, ...]] = {
    # arXiv-grade publish matrix: hybrid is cut (mcp_only + cli cover the tool-
    # access ablation; hybrid's incremental signal isn't worth its cost at
    # publish scale).
    "publish": ("baseline", "mcp_only", "cli"),
}

# ---------------------------------------------------------------------------
# Data
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class SweepItem:
    """One task+mode combination with its completion status."""

    task_id: str
    mode: str
    suite: str
    difficulty: str
    session_type: str
    toml_path: str
    status: str  # "scored", "pending", "failed"
    results_path: str | None  # path where results were found, or None


# ---------------------------------------------------------------------------
# Discovery
# ---------------------------------------------------------------------------


def discover_all_tasks(benchmarks_dir: Path | None = None) -> list[TaskInfo]:
    """Find all task.toml files under benchmarks/, excluding _archived."""
    if benchmarks_dir is None:
        benchmarks_dir = PROJECT_ROOT / "benchmarks"
    all_tasks = discover_tasks(benchmarks_dir)
    return [t for t in all_tasks if "/_archived/" not in str(t.toml_path)]


# ---------------------------------------------------------------------------
# Sweep matrix
# ---------------------------------------------------------------------------


def build_sweep_matrix(
    tasks: list[TaskInfo],
    modes: list[str] | None = None,
) -> list[SweepItem]:
    """Create all task x mode combinations as pending SweepItems."""
    if modes is None:
        modes = list(DEFAULT_SWEEP_MODES)
    items: list[SweepItem] = []
    for task in tasks:
        for mode in modes:
            items.append(
                SweepItem(
                    task_id=task.task_id,
                    mode=mode,
                    suite=task.suite,
                    difficulty=task.difficulty,
                    session_type=task.session_type,
                    toml_path=str(task.toml_path),
                    status="pending",
                    results_path=None,
                )
            )
    return items


# ---------------------------------------------------------------------------
# Completion checking
# ---------------------------------------------------------------------------


def _find_results_dirs(extra_dirs: list[Path] | None = None) -> list[Path]:
    """Collect all results directories to check for completion."""
    dirs: list[Path] = []

    # Always check results/runs
    runs_dir = PROJECT_ROOT / "results" / "runs"
    if runs_dir.is_dir():
        dirs.append(runs_dir)

    # Glob for mcp_batch* directories
    results_root = PROJECT_ROOT / "results"
    if results_root.is_dir():
        for d in sorted(results_root.iterdir()):
            if d.is_dir() and d.name.startswith("mcp_batch"):
                dirs.append(d)

    # Add any explicitly provided dirs
    if extra_dirs:
        for d in extra_dirs:
            resolved = d.resolve()
            if resolved.is_dir() and resolved not in dirs:
                dirs.append(resolved)

    return dirs


def _check_results_file(path: Path) -> str:
    """Check a single results.json and return 'scored' or 'failed'.

    Returns 'scored' if success is True, 'failed' otherwise.
    Raises FileNotFoundError if the file does not exist.
    """
    if not path.exists():
        raise FileNotFoundError(path)
    data = json.loads(path.read_text())
    if data.get("success") is True:
        return "scored"
    return "failed"


def _check_one_item(
    task_id: str,
    mode: str,
    results_dirs: list[Path],
) -> tuple[str, str | None]:
    """Check all candidate locations for a task+mode result.

    Returns (status, results_path) where status is scored/failed/pending.
    """
    candidates: list[Path] = []
    for rdir in results_dirs:
        # Multi-mode layout: results/runs/<task_id>/<mode>/results.json
        candidates.append(rdir / task_id / mode / "results.json")
        # Single-mode layout: results/runs/<task_id>/results.json (baseline only)
        if mode == "baseline":
            candidates.append(rdir / task_id / "results.json")
        # MCP batch layout: results/mcp_batch*/<task_id>_<mode>/results.json
        candidates.append(rdir / f"{task_id}_{mode}" / "results.json")

    best_status = "pending"
    best_path: str | None = None

    for cand in candidates:
        try:
            status = _check_results_file(cand)
        except (FileNotFoundError, json.JSONDecodeError, OSError):
            continue

        if status == "scored":
            return "scored", str(cand)
        # Track failed as a fallback — it means we found results but not success
        if status == "failed" and best_status == "pending":
            best_status = "failed"
            best_path = str(cand)

    return best_status, best_path


def check_completion(
    sweep_items: list[SweepItem],
    results_dirs: list[Path] | None = None,
) -> list[SweepItem]:
    """Mark each sweep item as scored/pending/failed by checking results dirs."""
    if results_dirs is None:
        results_dirs = _find_results_dirs()

    updated: list[SweepItem] = []
    for item in sweep_items:
        status, results_path = _check_one_item(item.task_id, item.mode, results_dirs)
        updated.append(
            SweepItem(
                task_id=item.task_id,
                mode=item.mode,
                suite=item.suite,
                difficulty=item.difficulty,
                session_type=item.session_type,
                toml_path=item.toml_path,
                status=status,
                results_path=results_path,
            )
        )
    return updated


# ---------------------------------------------------------------------------
# Manifest generation
# ---------------------------------------------------------------------------


def generate_manifest(sweep_items: list[SweepItem]) -> dict:
    """Build the sweep manifest dict from a list of SweepItems."""
    by_status: dict[str, int] = {}
    by_mode: dict[str, dict[str, int]] = {}

    for item in sweep_items:
        by_status[item.status] = by_status.get(item.status, 0) + 1

        if item.mode not in by_mode:
            by_mode[item.mode] = {}
        mode_counts = by_mode[item.mode]
        mode_counts[item.status] = mode_counts.get(item.status, 0) + 1

    manifest = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "total_combinations": len(sweep_items),
        "by_status": by_status,
        "by_mode": by_mode,
        "items": [
            {
                "task_id": item.task_id,
                "mode": item.mode,
                "suite": item.suite,
                "difficulty": item.difficulty,
                "status": item.status,
                "results_path": item.results_path,
            }
            for item in sweep_items
        ],
    }
    return manifest


# ---------------------------------------------------------------------------
# Run command generation
# ---------------------------------------------------------------------------


def generate_run_commands(
    pending_items: list[SweepItem],
    accounts: str = "1-5",
    model: str | None = None,
) -> list[str]:
    """Generate shell commands to run pending task+mode combos.

    Groups by mode and emits one run_benchmark.py invocation per mode. When
    *model* is given, `--model <model>` is appended to every command so a
    publish sweep pins the agent model end-to-end (run_sweep -> run_benchmark
    -> run_task -> the claude invocation) instead of leaving it to whatever the
    agent CLI defaults to that day.
    """
    modes_with_pending: set[str] = set()
    for item in pending_items:
        if item.status == "pending":
            modes_with_pending.add(item.mode)

    commands: list[str] = []
    for mode in sorted(modes_with_pending):
        cmd = (
            f"python3 scripts/run_benchmark.py benchmarks/ --all"
            f" --account {accounts} -j0 --mode {mode}"
        )
        if model:
            cmd += f" --model {model}"
        commands.append(cmd)
    return commands


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def _parse_accounts(raw: str) -> str:
    """Normalize account spec (e.g. '1-5' or '1,3,5')."""
    return raw.strip()


def build_parser() -> argparse.ArgumentParser:
    """Build the argparse parser."""
    parser = argparse.ArgumentParser(
        description="Three-mode sweep runner for EnterpriseBench.",
    )
    parser.add_argument(
        "--modes",
        default=None,
        help=(
            "Comma-separated list of modes (default: baseline,mcp_only,hybrid "
            "unless --matrix is given). Mutually exclusive with --matrix."
        ),
    )
    parser.add_argument(
        "--matrix",
        choices=sorted(MATRIX_PRESETS),
        default=None,
        help=(
            "Named mode-matrix preset instead of --modes. 'publish' = "
            "baseline,mcp_only,cli (the arXiv-grade sweep matrix, hybrid cut) "
            "— publish sweeps MUST pass --matrix publish; it is not the "
            "default. Mutually exclusive with --modes."
        ),
    )
    parser.add_argument(
        "--account",
        default="1-5",
        help="Account range (e.g. '1-5' or '1,3,5')",
    )
    parser.add_argument(
        "--model",
        default=None,
        help=(
            "Model to pin via --model on the generated run_benchmark.py "
            "commands (e.g. claude-sonnet-5). Recommended for publish sweeps "
            "so the run metadata records one model, not whatever the agent "
            "CLI defaults to that day."
        ),
    )
    parser.add_argument(
        "--manifest-only",
        action="store_true",
        help="Generate manifest without printing or executing run commands",
    )
    parser.add_argument(
        "--execute",
        action="store_true",
        help="Execute the generated run commands (otherwise just print them)",
    )
    parser.add_argument(
        "--output",
        default="configs/sweep_manifest.json",
        help="Path for the sweep manifest (default: configs/sweep_manifest.json)",
    )
    parser.add_argument(
        "--results-dir",
        action="append",
        dest="results_dirs",
        help="Additional results directory to check (repeatable)",
    )
    parser.add_argument(
        "-v",
        "--verbose",
        action="store_true",
        help="Enable debug logging",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    """Entry point for the sweep runner."""
    parser = build_parser()
    args = parser.parse_args(argv)

    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    )

    if args.modes and args.matrix:
        logger.error("--modes and --matrix are mutually exclusive")
        return 1
    if args.matrix:
        modes = list(MATRIX_PRESETS[args.matrix])
    elif args.modes:
        modes = [m.strip() for m in args.modes.split(",")]
    else:
        modes = list(DEFAULT_SWEEP_MODES)

    for m in modes:
        if m not in ALL_MODES:
            logger.error("Invalid mode: %s (valid: %s)", m, ", ".join(ALL_MODES))
            return 1

    accounts = _parse_accounts(args.account)

    # Extra results dirs from CLI
    extra_dirs: list[Path] | None = None
    if args.results_dirs:
        extra_dirs = [Path(d) for d in args.results_dirs]
    results_dirs = _find_results_dirs(extra_dirs)

    # 1. Discover tasks
    logger.info("Discovering tasks under benchmarks/...")
    tasks = discover_all_tasks()
    logger.info("Found %d tasks", len(tasks))

    # 2. Build sweep matrix
    sweep_items = build_sweep_matrix(tasks, modes)
    logger.info(
        "Sweep matrix: %d combinations (%d tasks x %d modes)",
        len(sweep_items),
        len(tasks),
        len(modes),
    )

    # 3. Check completion
    logger.info("Checking completion across %d results dirs...", len(results_dirs))
    sweep_items = check_completion(sweep_items, results_dirs)

    # 4. Generate and write manifest
    manifest = generate_manifest(sweep_items)
    output_path = Path(args.output)
    if not output_path.is_absolute():
        output_path = PROJECT_ROOT / output_path
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(json.dumps(manifest, indent=2) + "\n")
    logger.info("Manifest written to %s", output_path)

    # Summary
    by_status = manifest["by_status"]
    logger.info(
        "Status: scored=%d, pending=%d, failed=%d",
        by_status.get("scored", 0),
        by_status.get("pending", 0),
        by_status.get("failed", 0),
    )
    for mode, counts in manifest["by_mode"].items():
        logger.info(
            "  %s: scored=%d, pending=%d, failed=%d",
            mode,
            counts.get("scored", 0),
            counts.get("pending", 0),
            counts.get("failed", 0),
        )

    if args.manifest_only:
        return 0

    # 5. Generate run commands for pending items
    pending = [item for item in sweep_items if item.status == "pending"]
    if not pending:
        logger.info("No pending tasks — all combinations are scored or failed.")
        return 0

    commands = generate_run_commands(pending, accounts, model=args.model)
    logger.info("Run commands (%d):", len(commands))
    for cmd in commands:
        logger.info("  %s", cmd)

    # 6. Optionally execute
    if args.execute:
        for cmd in commands:
            logger.info("Executing: %s", cmd)
            result = subprocess.run(
                cmd,
                shell=True,
                cwd=str(PROJECT_ROOT),
            )
            if result.returncode != 0:
                logger.error(
                    "Command failed with exit code %d: %s", result.returncode, cmd
                )
                return result.returncode
        logger.info("All commands completed.")

    return 0


if __name__ == "__main__":
    sys.exit(main())
