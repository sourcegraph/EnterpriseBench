#!/usr/bin/env python3
"""
cost_tracker.py — Parse agent traces and compute per-task / aggregate costs.

Reads agent_trace.jsonl files from result directories, sums token usage per
task, applies Anthropic pricing, and writes a structured cost_report.json.
"""

from __future__ import annotations

import argparse
import json
import logging
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from lib.shared import load_task_index, strip_mode_suffix

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Pricing — per million tokens
# ---------------------------------------------------------------------------

PRICING: dict[str, dict[str, float]] = {
    "claude-sonnet-4-6": {
        "input": 3.0,
        "output": 15.0,
        "cache_write": 3.75,
        "cache_read": 0.30,
    },
    "claude-opus-4-6": {
        "input": 15.0,
        "output": 75.0,
        "cache_write": 18.75,
        "cache_read": 1.50,
    },
    "claude-haiku-4-5": {
        "input": 0.80,
        "output": 4.0,
        "cache_write": 1.0,
        "cache_read": 0.08,
    },
}

DEFAULT_MODEL = "claude-sonnet-4-6"

# ---------------------------------------------------------------------------
# Data classes
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class TraceUsage:
    """Aggregated token usage from a single trace file."""

    input_tokens: int
    output_tokens: int
    cache_write_tokens: int
    cache_read_tokens: int
    model: str
    num_requests: int


@dataclass(frozen=True)
class TaskCost:
    """Per-task cost record."""

    task_id: str
    mode: str
    suite: str
    difficulty: str
    usage: TraceUsage
    cost_usd: float
    agent_duration_seconds: float


# ---------------------------------------------------------------------------
# Core functions
# ---------------------------------------------------------------------------


def _request_key(entry: dict[str, Any]) -> str | None:
    """Return the key identifying the API request a trace line belongs to.

    A trace emits one assistant line per content block (thinking / text /
    tool_use), and every line of one API request repeats that request's usage
    snapshot. Grouping by request is what keeps a multi-block turn from being
    billed once per block.

    ``requestId`` carries the grouping in every assistant line of the current
    corpus; ``message.id`` is a verified 1:1 stand-in for a writer that omits
    it. None means the line announces no request of its own.
    """

    rid = entry.get("requestId")
    if isinstance(rid, str) and rid:
        return rid

    msg_id = (entry.get("message") or {}).get("id")
    if isinstance(msg_id, str) and msg_id:
        return msg_id

    return None


def parse_trace(trace_path: Path) -> TraceUsage:
    """Read an agent_trace.jsonl and sum token usage once per API request.

    Usage is deduplicated per request (see :func:`_request_key`); summing every
    assistant line instead billed one request once per content block.

    Within a request, the max-``output_tokens`` record is the one that carries
    the complete snapshot: input and cache counts are invariant across the
    request's lines, while ``output_tokens`` streams upward and is final only on
    the last one. Max rather than last also survives a trailing all-zero
    ``isApiErrorMessage`` record, which last-wins would bill at zero.

    Usage on non-assistant lines (sub-agent / Task tool results) is not counted
    — see EnterpriseBench-jepu.
    """

    # request key -> that request's most complete usage snapshot. A line with no
    # request key of its own is keyed by line number (an int, so it can never
    # collide with a requestId) and therefore billed exactly once — correct for
    # legacy single-block traces, where each line already is its own request.
    selected: dict[str | int, dict[str, Any]] = {}
    model = ""
    ungrouped_lines = 0

    with trace_path.open() as fh:
        for line_no, line in enumerate(fh):
            line = line.strip()
            if not line:
                continue
            try:
                entry = json.loads(line)
            except json.JSONDecodeError:
                logger.warning("Skipping malformed line in %s", trace_path)
                continue

            if entry.get("type") != "assistant":
                continue

            msg = entry.get("message", {})
            if not msg:
                continue

            # Capture the model from the first assistant message that has one
            msg_model = msg.get("model", "")
            if msg_model and not model:
                model = msg_model

            usage = msg.get("usage", {})
            if not usage:
                continue

            key = _request_key(entry)
            if key is None:
                key = line_no
                ungrouped_lines += 1

            previous = selected.get(key)
            if previous is None or usage.get("output_tokens", 0) >= previous.get(
                "output_tokens", 0
            ):
                selected[key] = usage

    if ungrouped_lines:
        logger.warning(
            "%s: %d assistant line(s) carry neither requestId nor message.id, so "
            "each was billed as its own request. Correct for legacy single-block "
            "traces; if the current format dropped those keys, this undercounts "
            "grouping and cost is inflated again.",
            trace_path,
            ungrouped_lines,
        )

    def total(field: str) -> int:
        return sum(u.get(field, 0) for u in selected.values())

    return TraceUsage(
        input_tokens=total("input_tokens"),
        output_tokens=total("output_tokens"),
        cache_write_tokens=total("cache_creation_input_tokens"),
        cache_read_tokens=total("cache_read_input_tokens"),
        model=model or DEFAULT_MODEL,
        num_requests=len(selected),
    )


def compute_cost(usage: TraceUsage, model: str | None = None) -> float:
    """Return USD cost for a TraceUsage given Anthropic pricing."""

    resolved_model = model or usage.model or DEFAULT_MODEL
    prices = PRICING.get(resolved_model)
    if prices is None:
        logger.warning(
            "Unknown model %r — falling back to %s pricing",
            resolved_model,
            DEFAULT_MODEL,
        )
        prices = PRICING[DEFAULT_MODEL]

    cost = (
        usage.input_tokens * prices["input"]
        + usage.output_tokens * prices["output"]
        + usage.cache_write_tokens * prices["cache_write"]
        + usage.cache_read_tokens * prices["cache_read"]
    ) / 1_000_000

    return round(cost, 6)


# ---------------------------------------------------------------------------
# Task metadata lookup
# ---------------------------------------------------------------------------

# Module-level cache for task metadata (populated lazily by _get_task_meta).
_TASK_META_CACHE: dict[str, dict[str, str]] = {}


def _get_task_meta(task_id: str, benchmarks_root: Path) -> dict[str, str]:
    """Return {suite, difficulty} for a task_id, reading benchmarks if needed."""

    if not _TASK_META_CACHE:
        index = load_task_index(benchmarks_root)
        for tid, meta in index.items():
            _TASK_META_CACHE[tid] = {
                "suite": meta.get("suite", "unknown"),
                "difficulty": meta.get("difficulty", "unknown"),
            }

    return _TASK_META_CACHE.get(task_id, {"suite": "unknown", "difficulty": "unknown"})


# ---------------------------------------------------------------------------
# Directory scanning
# ---------------------------------------------------------------------------


def _parse_dir_identity(dir_path: Path) -> tuple[str, str]:
    """Infer (task_id, mode) from a results directory path.

    - results/runs/<task_id>/<mode>/     -> multi-mode layout (new)
    - results/runs/<task_id>/            -> mode = "baseline" (legacy)
    - results/mcp_batch*/<id>_<mode>/    -> parse mode from suffix
    """

    name = dir_path.name
    parent_name = dir_path.parent.name
    grandparent_name = dir_path.parent.parent.name if dir_path.parent.parent else ""

    # Multi-mode layout: results/runs/<task_id>/<mode>/
    if name in ("baseline", "mcp_only", "hybrid") and grandparent_name == "runs":
        return parent_name, name

    # Legacy single-mode: results/runs/<task_id>/
    if parent_name == "runs":
        return name, "baseline"

    task_id, mode = strip_mode_suffix(name)
    # strip_mode_suffix defaults to "baseline" when no suffix found;
    # for non-runs directories without a suffix, treat as "unknown".
    if mode == "baseline" and not name.endswith("_baseline"):
        return name, "unknown"
    return task_id, mode


def scan_results_dirs(
    dirs: list[Path],
    benchmarks_root: Path,
) -> list[TaskCost]:
    """Find all result directories containing agent_trace.jsonl and compute costs."""

    costs: list[TaskCost] = []

    for root_dir in dirs:
        if not root_dir.is_dir():
            logger.info("Skipping missing directory: %s", root_dir)
            continue

        for trace_path in sorted(root_dir.rglob("agent_trace.jsonl")):
            task_dir = trace_path.parent
            task_id, mode = _parse_dir_identity(task_dir)
            meta = _get_task_meta(task_id, benchmarks_root)

            usage = parse_trace(trace_path)
            cost = compute_cost(usage)

            # Try to get agent duration from task_metrics.json
            duration = 0.0
            metrics_path = task_dir / "task_metrics.json"
            if metrics_path.exists():
                try:
                    with metrics_path.open() as fh:
                        metrics = json.load(fh)
                    duration = metrics.get("timing", {}).get("agent", 0.0)
                except Exception:
                    logger.warning("Failed to read %s", metrics_path)

            costs.append(
                TaskCost(
                    task_id=task_id,
                    mode=mode,
                    suite=meta["suite"],
                    difficulty=meta["difficulty"],
                    usage=usage,
                    cost_usd=cost,
                    agent_duration_seconds=duration,
                )
            )

    return costs


# ---------------------------------------------------------------------------
# Aggregation
# ---------------------------------------------------------------------------


def _bucket_stats(items: list[TaskCost]) -> dict[str, Any]:
    """Compute summary stats for a list of TaskCost records."""

    count = len(items)
    total_cost = round(sum(t.cost_usd for t in items), 6)
    total_input = sum(t.usage.input_tokens for t in items)
    total_output = sum(t.usage.output_tokens for t in items)
    return {
        "count": count,
        "total_cost": total_cost,
        "avg_cost": round(total_cost / count, 6) if count else 0.0,
        "total_input_tokens": total_input,
        "total_output_tokens": total_output,
    }


def aggregate_report(costs: list[TaskCost]) -> dict[str, Any]:
    """Build the full cost report with suite/mode/difficulty breakdowns."""

    by_mode: dict[str, list[TaskCost]] = {}
    by_suite: dict[str, list[TaskCost]] = {}
    by_difficulty: dict[str, list[TaskCost]] = {}

    for tc in costs:
        by_mode.setdefault(tc.mode, []).append(tc)
        by_suite.setdefault(tc.suite, []).append(tc)
        by_difficulty.setdefault(tc.difficulty, []).append(tc)

    per_task = [
        {
            "task_id": tc.task_id,
            "mode": tc.mode,
            "suite": tc.suite,
            "difficulty": tc.difficulty,
            "model": tc.usage.model,
            "input_tokens": tc.usage.input_tokens,
            "output_tokens": tc.usage.output_tokens,
            "cache_write_tokens": tc.usage.cache_write_tokens,
            "cache_read_tokens": tc.usage.cache_read_tokens,
            "num_requests": tc.usage.num_requests,
            "cost_usd": tc.cost_usd,
            "agent_duration_seconds": tc.agent_duration_seconds,
        }
        for tc in sorted(costs, key=lambda c: c.task_id)
    ]

    # compute_cost bills an unpriced model at DEFAULT_MODEL rates. Those models
    # cluster in a single arm, so the substitution skews arm-to-arm cost deltas,
    # not just absolute cost. The caveat rides in the report itself — a warning
    # in a batch log does not reach whoever reads the JSON.
    unpriced_models = sorted(
        {tc.usage.model for tc in costs if tc.usage.model not in PRICING}
    )
    if unpriced_models:
        logger.warning(
            "Unpriced model(s) billed at %s rates: %s. Absolute costs and "
            "arm-to-arm deltas involving these tasks are not trustworthy.",
            DEFAULT_MODEL,
            ", ".join(unpriced_models),
        )

    return {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "total_cost_usd": round(sum(tc.cost_usd for tc in costs), 6),
        "total_tasks": len(costs),
        "unpriced_models": unpriced_models,
        "by_mode": {k: _bucket_stats(v) for k, v in sorted(by_mode.items())},
        "by_suite": {k: _bucket_stats(v) for k, v in sorted(by_suite.items())},
        "by_difficulty": {
            k: _bucket_stats(v) for k, v in sorted(by_difficulty.items())
        },
        "per_task": per_task,
    }


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def _discover_default_dirs(project_root: Path) -> list[Path]:
    """Return default result directories: results/runs + results/mcp_batch*."""

    results_dir = project_root / "results"
    dirs: list[Path] = []

    runs = results_dir / "runs"
    if runs.is_dir():
        dirs.append(runs)

    for p in sorted(results_dir.iterdir()) if results_dir.is_dir() else []:
        if p.is_dir() and p.name.startswith("mcp_batch"):
            dirs.append(p)

    return dirs


def main(argv: list[str] | None = None) -> None:
    """CLI entry point."""

    parser = argparse.ArgumentParser(
        description="Aggregate token usage and costs from EnterpriseBench runs."
    )
    parser.add_argument(
        "--results-dir",
        type=Path,
        action="append",
        default=None,
        help="Result directory to scan (repeatable). "
        "Defaults to results/runs + results/mcp_batch*.",
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=None,
        help="Output path for cost_report.json (default: results/cost_report.json).",
    )
    parser.add_argument(
        "--benchmarks-root",
        type=Path,
        default=None,
        help="Benchmarks directory for task metadata (default: benchmarks/).",
    )
    parser.add_argument(
        "-v",
        "--verbose",
        action="store_true",
        help="Enable debug logging.",
    )

    args = parser.parse_args(argv)

    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(levelname)s: %(message)s",
    )

    project_root = Path(__file__).resolve().parent.parent
    benchmarks_root = args.benchmarks_root or (project_root / "benchmarks")
    output_path = args.output or (project_root / "results" / "cost_report.json")
    result_dirs = args.results_dir or _discover_default_dirs(project_root)

    if not result_dirs:
        logger.error("No result directories found.")
        return

    logger.info("Scanning %d result directories...", len(result_dirs))
    costs = scan_results_dirs(result_dirs, benchmarks_root)
    logger.info("Found %d tasks with trace data.", len(costs))

    report = aggregate_report(costs)

    output_path.parent.mkdir(parents=True, exist_ok=True)
    with output_path.open("w") as fh:
        json.dump(report, fh, indent=2)
    logger.info("Report written to %s", output_path)


if __name__ == "__main__":
    main()
