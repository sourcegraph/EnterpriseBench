#!/usr/bin/env python3
"""Batch orchestrator for EnterpriseBench Phase 1 pilot runs.

Reads a manifest JSON, assigns account IDs (1-5), executes runs in parallel,
tracks results, and produces a summary CSV.

Usage:
    python3 scripts/orchestration/run_pilot.py --manifest configs/pilot_manifest.json
    python3 scripts/orchestration/run_pilot.py --manifest configs/pilot_manifest.json --dry-run
    python3 scripts/orchestration/run_pilot.py --manifest configs/pilot_manifest.json --max-parallel 3
"""

import argparse
import csv
import json
import logging
import subprocess
import sys
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
RUN_TASK_SCRIPT = REPO_ROOT / "scripts" / "orchestration" / "run_task.py"
ABLATION_SCRIPT = REPO_ROOT / "scripts" / "validation" / "run_crnt_ablation.sh"
RESULTS_DIR = REPO_ROOT / "results" / "phase1_pilot"

VALID_MODES = frozenset(
    {
        "baseline",
        "mcp_only",
        "mcp_default",
        "mcp_treatment",
    "mcp_treat_instr",
    "mcp_treat_edges",
    "mcp_treat_nudge",
        "mcp_code_finder",
        "mcp_assisted",
        "hybrid",
        "cli",
    }
)
REQUIRED_FIELDS = frozenset(
    {"task_id", "task_dir", "mode", "rep_index", "account_id", "output_dir"}
)


@dataclass(frozen=True)
class RunEntry:
    """Immutable description of a single pilot run."""

    task_id: str
    task_dir: str
    mode: str
    rep_index: int
    account_id: int
    output_dir: str


@dataclass(frozen=True)
class RunResult:
    """Immutable result of a single pilot run."""

    task_id: str
    mode: str
    rep_index: int
    account_id: int
    output_dir: str
    status: str = "pending"
    score: Optional[float] = None
    error: str = ""
    started_at: str = ""
    finished_at: str = ""


def load_manifest(manifest_path: Path) -> list[RunEntry]:
    """Load and validate a pilot manifest JSON file.

    Returns a list of RunEntry objects. Raises ValueError on validation failure.
    """
    if not manifest_path.is_file():
        raise FileNotFoundError(f"Manifest not found: {manifest_path}")

    data = json.loads(manifest_path.read_text())

    if not isinstance(data, list):
        raise ValueError("Manifest must be a JSON array")

    entries: list[RunEntry] = []
    for i, item in enumerate(data):
        missing = REQUIRED_FIELDS - set(item.keys())
        if missing:
            raise ValueError(f"Entry {i} missing fields: {missing}")

        if not isinstance(item["rep_index"], int) or item["rep_index"] < 1:
            raise ValueError(f"Entry {i}: rep_index must be a positive integer")

        if not isinstance(item["account_id"], int) or not 1 <= item["account_id"] <= 5:
            raise ValueError(f"Entry {i}: account_id must be 1-5")

        entries.append(
            RunEntry(
                task_id=item["task_id"],
                task_dir=item["task_dir"],
                mode=item["mode"],
                rep_index=item["rep_index"],
                account_id=item["account_id"],
                output_dir=item["output_dir"],
            )
        )

    return entries


def validate_output_paths(entries: list[RunEntry]) -> None:
    """Validate that output paths follow the expected convention."""
    for entry in entries:
        expected = f"results/runs/{entry.task_id}/{entry.mode}/rep{entry.rep_index}/"
        if entry.output_dir != expected:
            raise ValueError(
                f"Output path mismatch for {entry.task_id}/{entry.mode}/rep{entry.rep_index}: "
                f"expected {expected!r}, got {entry.output_dir!r}"
            )


def is_ablation_run(entry: RunEntry) -> bool:
    """Check if a run entry is an ablation run."""
    return entry.mode.startswith("ablate-")


def _build_run_command(entry: RunEntry) -> list[str]:
    """Build the subprocess command for a run entry."""
    task_toml = str(REPO_ROOT / entry.task_dir / "task.toml")
    output_dir = str(REPO_ROOT / entry.output_dir)

    if is_ablation_run(entry):
        # Ablation runs use run_crnt_ablation.sh
        excluded_repo = entry.mode.removeprefix("ablate-")
        return [
            "bash",
            str(ABLATION_SCRIPT),
            str(REPO_ROOT / entry.task_dir),
            "--reps",
            "1",
            "--mode",
            "baseline",
            "--repo",
            excluded_repo,
        ]

    # Full runs use run_task.py
    cmd = [
        sys.executable,
        str(RUN_TASK_SCRIPT),
        task_toml,
        "--mode",
        entry.mode,
        "--output-dir",
        output_dir,
        "--account",
        str(entry.account_id),
        "--rep",
        str(entry.rep_index),
        "--source",
        "upstream",
    ]

    # Pass ablation variant for ablate-* modes so Docker tags don't collide
    if is_ablation_run(entry):
        excluded_repo = entry.mode.removeprefix("ablate-")
        cmd.extend(["--ablation-variant", excluded_repo])

    return cmd


def execute_run(entry: RunEntry, dry_run: bool = False) -> RunResult:
    """Execute a single run and return the result."""
    started_at = datetime.now(timezone.utc).isoformat()
    cmd = _build_run_command(entry)

    if dry_run:
        logger.info("[DRY-RUN] Would execute: %s", " ".join(cmd))
        return RunResult(
            task_id=entry.task_id,
            mode=entry.mode,
            rep_index=entry.rep_index,
            account_id=entry.account_id,
            output_dir=entry.output_dir,
            status="dry-run",
            started_at=started_at,
            finished_at=datetime.now(timezone.utc).isoformat(),
        )

    try:
        output_path = REPO_ROOT / entry.output_dir
        output_path.mkdir(parents=True, exist_ok=True)

        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=3600,
            cwd=str(REPO_ROOT),
        )

        finished_at = datetime.now(timezone.utc).isoformat()

        # Try to read score from results.json if it exists
        score = _read_score(output_path)

        if result.returncode == 0:
            return RunResult(
                task_id=entry.task_id,
                mode=entry.mode,
                rep_index=entry.rep_index,
                account_id=entry.account_id,
                output_dir=entry.output_dir,
                status="completed",
                score=score,
                started_at=started_at,
                finished_at=finished_at,
            )

        return RunResult(
            task_id=entry.task_id,
            mode=entry.mode,
            rep_index=entry.rep_index,
            account_id=entry.account_id,
            output_dir=entry.output_dir,
            status="failed",
            score=score,
            error=(
                result.stderr[-500:]
                if result.stderr
                else f"exit code {result.returncode}"
            ),
            started_at=started_at,
            finished_at=finished_at,
        )

    except subprocess.TimeoutExpired:
        return RunResult(
            task_id=entry.task_id,
            mode=entry.mode,
            rep_index=entry.rep_index,
            account_id=entry.account_id,
            output_dir=entry.output_dir,
            status="timeout",
            error="Run exceeded 3600s timeout",
            started_at=started_at,
            finished_at=datetime.now(timezone.utc).isoformat(),
        )
    except Exception as exc:
        return RunResult(
            task_id=entry.task_id,
            mode=entry.mode,
            rep_index=entry.rep_index,
            account_id=entry.account_id,
            output_dir=entry.output_dir,
            status="error",
            error=str(exc)[:500],
            started_at=started_at,
            finished_at=datetime.now(timezone.utc).isoformat(),
        )


def _read_score(output_path: Path) -> Optional[float]:
    """Read aggregate score from results.json if present."""
    results_file = output_path / "results.json"
    if not results_file.is_file():
        return None
    try:
        data = json.loads(results_file.read_text())
        scores = data.get("scores", {})
        if scores:
            values = [float(v) for v in scores.values()]
            return sum(values) / len(values)
    except (json.JSONDecodeError, TypeError, ValueError):
        pass
    return None


def write_run_manifest(results: list[RunResult], output_path: Path) -> None:
    """Write the run manifest JSON tracking all run entries with status/score."""
    output_path.parent.mkdir(parents=True, exist_ok=True)
    manifest_data = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "total_runs": len(results),
        "by_status": {},
        "entries": [asdict(r) for r in results],
    }

    # Count by status
    for r in results:
        manifest_data["by_status"][r.status] = (
            manifest_data["by_status"].get(r.status, 0) + 1
        )

    output_path.write_text(json.dumps(manifest_data, indent=2) + "\n")
    logger.info("Run manifest written: %s", output_path)


def write_summary_csv(results: list[RunResult], output_path: Path) -> None:
    """Write a summary CSV of all run results."""
    output_path.parent.mkdir(parents=True, exist_ok=True)
    fieldnames = [
        "task_id",
        "mode",
        "rep_index",
        "account_id",
        "status",
        "score",
        "error",
    ]

    with output_path.open("w", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        for r in results:
            writer.writerow(
                {
                    "task_id": r.task_id,
                    "mode": r.mode,
                    "rep_index": r.rep_index,
                    "account_id": r.account_id,
                    "status": r.status,
                    "score": r.score if r.score is not None else "",
                    "error": r.error,
                }
            )

    logger.info("Summary CSV written: %s", output_path)


def run_pilot(
    manifest_path: Path,
    dry_run: bool = False,
    max_parallel: int = 5,
) -> list[RunResult]:
    """Execute all runs from the manifest in parallel.

    Groups runs by account_id and executes up to max_parallel concurrently.
    Returns a list of RunResult objects.
    """
    entries = load_manifest(manifest_path)
    validate_output_paths(entries)

    logger.info(
        "Loaded %d runs from manifest (dry_run=%s, max_parallel=%d)",
        len(entries),
        dry_run,
        max_parallel,
    )

    results: list[RunResult] = []

    with ThreadPoolExecutor(max_workers=max_parallel) as executor:
        future_to_entry = {
            executor.submit(execute_run, entry, dry_run): entry for entry in entries
        }

        for future in as_completed(future_to_entry):
            entry = future_to_entry[future]
            try:
                result = future.result()
                results.append(result)
                logger.info(
                    "[%s] %s/%s/rep%d -> %s (score=%s)",
                    result.status,
                    result.task_id,
                    result.mode,
                    result.rep_index,
                    result.output_dir,
                    result.score,
                )
            except Exception as exc:
                logger.error("Unexpected error for %s: %s", entry.task_id, exc)
                results.append(
                    RunResult(
                        task_id=entry.task_id,
                        mode=entry.mode,
                        rep_index=entry.rep_index,
                        account_id=entry.account_id,
                        output_dir=entry.output_dir,
                        status="error",
                        error=str(exc)[:500],
                    )
                )

    # Sort results to match manifest order for deterministic output
    entry_order = {(e.task_id, e.mode, e.rep_index): i for i, e in enumerate(entries)}
    results.sort(key=lambda r: entry_order.get((r.task_id, r.mode, r.rep_index), 999))

    # Write outputs
    run_manifest_path = RESULTS_DIR / "run_manifest.json"
    summary_csv_path = RESULTS_DIR / "summary.csv"

    write_run_manifest(results, run_manifest_path)
    write_summary_csv(results, summary_csv_path)

    return results


def main() -> None:
    """CLI entry point."""
    parser = argparse.ArgumentParser(
        description="Batch orchestrator for EnterpriseBench Phase 1 pilot runs.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""\
Examples:
  %(prog)s --manifest configs/pilot_manifest.json
  %(prog)s --manifest configs/pilot_manifest.json --dry-run
  %(prog)s --manifest configs/pilot_manifest.json --max-parallel 3
""",
    )
    parser.add_argument(
        "--manifest",
        type=Path,
        required=True,
        help="Path to the pilot manifest JSON file",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        default=False,
        help="Print what would be executed without actually running anything",
    )
    parser.add_argument(
        "--max-parallel",
        type=int,
        default=5,
        help="Maximum number of parallel runs (default: 5)",
    )

    args = parser.parse_args()

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)-8s %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )

    results = run_pilot(
        manifest_path=args.manifest,
        dry_run=args.dry_run,
        max_parallel=args.max_parallel,
    )

    # Print summary
    completed = sum(1 for r in results if r.status == "completed")
    failed = sum(1 for r in results if r.status == "failed")
    dry_runs = sum(1 for r in results if r.status == "dry-run")

    print(
        f"\nPilot run complete: {len(results)} total, "
        f"{completed} completed, {failed} failed, {dry_runs} dry-run"
    )
    print(f"Run manifest: {RESULTS_DIR / 'run_manifest.json'}")
    print(f"Summary CSV:  {RESULTS_DIR / 'summary.csv'}")


if __name__ == "__main__":
    main()
