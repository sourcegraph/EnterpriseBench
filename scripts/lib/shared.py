"""Shared utilities for EnterpriseBench analysis scripts."""

from __future__ import annotations

import json
import logging
from pathlib import Path

try:
    import tomllib
except ImportError:
    import tomli as tomllib  # type: ignore[no-redefine]

logger = logging.getLogger(__name__)

PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent

VALID_MODES = ("baseline", "mcp_only", "hybrid", "cli")
# Longest/most-specific suffixes first so "_mcp_only" cannot be shadowed by a
# shorter false match. Every entry here must have a matching VALID_MODES arm —
# the "cli" arm was added to VALID_MODES without an "_cli" suffix here, which
# silently mis-parsed cli result directories as (dirname, "baseline").
MODE_SUFFIXES = ("_hybrid", "_mcp_only", "_cli", "_baseline")

# Canonical arm ordering for the publish matrix: baseline first (the
# comparison point), then mcp_only, then cli, then hybrid if a result set
# still carries it (hybrid is cut from the publish sweep, but old data may
# still have it). The single source of truth for analyze_scores.py,
# generate_charts.py, and generate_report.py — each previously hard-coded its
# own {baseline, hybrid, mcp_only} arm set and silently dropped or mislabeled
# "cli".
MODE_ORDER = ("baseline", "mcp_only", "cli", "hybrid")


def ordered_modes(mode_keys) -> list[str]:
    """Order an arbitrary collection of arm names per MODE_ORDER.

    Arms present in *mode_keys* but not in MODE_ORDER are appended
    alphabetically rather than dropped, so a reporting function using this
    never silently loses an arm the data actually contains.
    """
    present = set(mode_keys)
    ordered = [m for m in MODE_ORDER if m in present]
    ordered += sorted(present - set(ordered))
    return ordered


def strip_mode_suffix(dirname: str) -> tuple[str, str]:
    """Strip mode suffix from directory name, return (task_id, mode).

    Examples:
        'cal-err-flask-001_hybrid' -> ('cal-err-flask-001', 'hybrid')
        'cal-err-flask-001' -> ('cal-err-flask-001', 'baseline')
    """
    for suffix in MODE_SUFFIXES:
        if dirname.endswith(suffix):
            return dirname[: -len(suffix)], suffix.lstrip("_")
    return dirname, "baseline"


def discover_results_dirs(root: Path | None = None) -> list[Path]:
    """Find all results directories (runs, mcp_batch*, smoke_*)."""
    if root is None:
        root = PROJECT_ROOT / "results"
    dirs: list[Path] = []
    runs = root / "runs"
    if runs.is_dir():
        dirs.append(runs)
    for d in sorted(root.iterdir()) if root.is_dir() else []:
        if (
            d.is_dir()
            and d.name != "runs"
            and (d.name.startswith("mcp_batch") or d.name.startswith("smoke_"))
        ):
            dirs.append(d)
    return dirs


def load_task_index(
    benchmarks_root: Path | None = None,
) -> dict[str, dict[str, str]]:
    """Build task_id -> {suite, difficulty, task_type, ...} index from task.toml files."""
    if benchmarks_root is None:
        benchmarks_root = PROJECT_ROOT / "benchmarks"
    index: dict[str, dict[str, str]] = {}
    for toml_path in benchmarks_root.rglob("task.toml"):
        try:
            with open(toml_path, "rb") as f:
                data = tomllib.load(f)
            task = data.get("task", {})
            tid = task.get("id", toml_path.parent.name)
            index[tid] = {
                "suite": task.get("suite", ""),
                "difficulty": task.get("difficulty", ""),
                "task_type": task.get("task_type", ""),
                "session_type": task.get("session_type", "single"),
            }
        except Exception as exc:
            logger.debug("Failed to parse %s: %s", toml_path, exc)
    return index
