"""Shared utilities for EnterpriseBench analysis scripts."""

from __future__ import annotations

import logging
from pathlib import Path

try:
    import tomllib
except ImportError:
    import tomli as tomllib  # type: ignore[no-redefine]

logger = logging.getLogger(__name__)

PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent

VALID_MODES = (
    "baseline",
    "mcp_only",
    "mcp_default",
    "mcp_treatment",
    "mcp_treat_instr",
    "mcp_treat_edges",
    "mcp_treat_nudge",
    "mcp_code_finder",
    "mcp_assisted",
    "mcp_toast",
    "hybrid",
    "cli",
)
MODE_SUFFIXES = (
    "_mcp_code_finder",
    "_mcp_default",
    "_mcp_treatment",
    "_mcp_treat_instr",
    "_mcp_treat_edges",
    "_mcp_treat_nudge",
    "_mcp_assisted",
    "_mcp_toast",
    "_hybrid",
    "_mcp_only",
    "_baseline",
)

# Separates a mode from a variant label in a run directory name
# (results/runs/<task_id>/<mode>--<label>/). "--" cannot appear in a mode name
# (single underscores) or inside a label (the run_task CLI validator forbids
# consecutive hyphens), so splitting on the first occurrence is total.
VARIANT_LABEL_SEPARATOR = "--"


def split_variant_label(dirname: str) -> tuple[str, str | None]:
    """Split '<mode>--<label>' into (mode, label); label is None when absent."""
    if VARIANT_LABEL_SEPARATOR not in dirname:
        return dirname, None
    mode, _, label = dirname.partition(VARIANT_LABEL_SEPARATOR)
    return mode, label


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
