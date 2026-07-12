#!/usr/bin/env python3
"""
cost_tracker.py — Report per-task / aggregate costs for EnterpriseBench runs.

Cost comes from the vendor wherever the vendor reported it. Claude Code writes a
per-model ``modelUsage`` block to agent_stdout.log carrying its own ``costUSD``
alongside input / output / cache token counts; that block is authoritative and is
what this module bills from (tier 1).

Only when a run has no such block does this module fall back to re-deriving cost
from agent_trace.jsonl against the local PRICING table (tier 2). That derivation
is lossy and must not be trusted as a primary source: the trace records a single
model per run and never carries sub-agent usage at all, so a multi-model run
cannot be priced correctly from it no matter how carefully the usage is summed
(EnterpriseBench-qc7f, -jepu). Every fallback run is disclosed in the report.

Both numbers are computed for every run, and their disagreement is published as a
reconciliation block — a cost source that silently diverges from the trace is the
failure this module exists to make visible.
"""

from __future__ import annotations

import argparse
import json
import logging
import math
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from lib.shared import load_task_index, strip_mode_suffix

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Pricing — per million tokens
#
# Used only for tier-2 (trace-derived) runs. Tier-1 runs are billed with the
# vendor's own costUSD, which prices every model it saw — including ones this
# table has never heard of (claude-opus-4-8, claude-fable-5).
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

# Label for the flat modelUsage shape, which names no model. Vendor-priced, so it
# never needs a PRICING entry.
UNKNOWN_MODEL = "unknown"

# How far the per-model costUSD records may drift from the vendor's own
# total_cost_usd before the block is treated as incomplete. Floating-point
# summation of a handful of values needs only the absolute floor; the relative
# term is slack for a vendor that rounds its total.
COST_RECONCILE_ABS = 1e-6
COST_RECONCILE_REL = 0.005

# ---------------------------------------------------------------------------
# Data classes
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class ModelUsage:
    """One model's usage for one run, exactly as the vendor reported it.

    ``cost_usd`` is the vendor's own price for this model's tokens, not a local
    re-derivation, so it is correct even for a model absent from PRICING.
    """

    model: str
    input_tokens: int
    output_tokens: int
    cache_write_tokens: int
    cache_read_tokens: int
    cost_usd: float


@dataclass(frozen=True)
class VendorUsage:
    """One run's usage as the vendor reported it, with the vendor's own bottom line.

    ``models`` carries the per-model split (tokens, both cache columns included)
    that the report needs and that the flattened task_metrics.json copy of this
    same data throws away.
    """

    models: tuple[ModelUsage, ...]
    total_cost_usd: float


@dataclass(frozen=True)
class Usage:
    """Aggregated token usage for one run.

    Tokens come from the vendor's modelUsage block when the run has one, and from
    the trace otherwise; :attr:`TaskCost.cost_source` records which. ``model`` is
    the single largest spender — a representative label, with the full set on
    :attr:`TaskCost.models`. ``num_requests`` is always trace-derived; the vendor
    block reports no request count.
    """

    input_tokens: int
    output_tokens: int
    cache_write_tokens: int
    cache_read_tokens: int
    model: str
    num_requests: int


@dataclass(frozen=True)
class TaskCost:
    """Per-task cost record.

    ``cost_usd`` is authoritative. ``trace_cost_usd`` is what the old trace
    derivation would have billed, retained for every run so the two can be
    reconciled; on a tier-2 run the two are equal by construction.
    """

    task_id: str
    mode: str
    suite: str
    difficulty: str
    usage: Usage
    cost_usd: float
    cost_source: str  # "sdk" (vendor modelUsage) | "trace" (PRICING derivation)
    trace_cost_usd: float
    models: tuple[str, ...]
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


def parse_trace(trace_path: Path) -> Usage:
    """Read an agent_trace.jsonl and sum token usage once per API request.

    The tier-2 (fallback) token source, reached only when a run carries no vendor
    block; the module docstring says why it cannot be the primary one.

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

    return Usage(
        input_tokens=total("input_tokens"),
        output_tokens=total("output_tokens"),
        cache_write_tokens=total("cache_creation_input_tokens"),
        cache_read_tokens=total("cache_read_input_tokens"),
        model=model or DEFAULT_MODEL,
        num_requests=len(selected),
    )


# ---------------------------------------------------------------------------
# Vendor usage (tier 1) — Claude Code's own modelUsage block
# ---------------------------------------------------------------------------


def _finite(value: Any, model: str, field: str) -> float:
    """Return ``value`` as a float, rejecting anything that is not a real number.

    ``json.loads`` accepts the non-standard literals ``NaN`` / ``Infinity``, and a
    NaN would be catastrophic here precisely because it is quiet: every comparison
    against NaN is False, so a NaN cost slips through the reconciliation gate below
    untouched and then poisons the batch total — one unreadable run silently
    turning the whole report's bottom line into NaN. Reject it at the boundary
    instead, where it becomes a disclosed fallback rather than a wrong number.
    """

    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError(f"model {model!r} field {field} is {value!r}, not a number")
    if not math.isfinite(value):
        raise ValueError(f"model {model!r} field {field} is non-finite ({value!r})")
    return float(value)


def _count(value: Any, model: str, field: str) -> int:
    """Return a token count as an int. Absent means zero; unreadable means reject.

    The :func:`_finite` call has to come before the ``int()``, and that ordering is
    the whole safety property: ``int(float("inf"))`` raises OverflowError, which is
    not a ValueError and so would sail past the caller's fallback handler and abort
    the entire batch scan over one malformed log. Validating first means ``int()``
    only ever sees a finite number and the overflow is unreachable.
    """

    if value is None:
        return 0
    return int(_finite(value, model, field))


def _model_usage_records(block: dict[str, Any]) -> tuple[ModelUsage, ...]:
    """Convert a raw modelUsage mapping into per-model records.

    The block comes in two shapes, matching run_task.py::_sum_model_usage:
    flat (``{"inputTokens": N, ...}``, one anonymous model) or per-model
    (``{"claude-sonnet-4-6": {...}, ...}``). Every run in the current corpus is
    per-model; flat is handled because the writer that produces this file still
    emits it.

    Parsing is strict, and deliberately so: raises ValueError on anything it
    cannot read whole rather than skipping the entry, because a skipped model
    still bills the run, just for less. The caller turns that into a loud
    fallback.
    """

    def record(model: str, entry: Any) -> ModelUsage:
        if not isinstance(entry, dict):
            raise ValueError(
                f"model {model!r} maps to {type(entry).__name__}, not an object"
            )
        if "costUSD" not in entry:
            raise ValueError(f"model {model!r} carries no costUSD")
        return ModelUsage(
            model=model,
            input_tokens=_count(entry.get("inputTokens"), model, "inputTokens"),
            output_tokens=_count(entry.get("outputTokens"), model, "outputTokens"),
            cache_write_tokens=_count(
                entry.get("cacheCreationInputTokens"), model, "cacheCreationInputTokens"
            ),
            cache_read_tokens=_count(
                entry.get("cacheReadInputTokens"), model, "cacheReadInputTokens"
            ),
            cost_usd=_finite(entry["costUSD"], model, "costUSD"),
        )

    if "inputTokens" in block:
        if any(isinstance(value, dict) for value in block.values()):
            raise ValueError("block mixes the flat and per-model shapes")
        return (record(UNKNOWN_MODEL, block),)

    return tuple(record(name, entry) for name, entry in sorted(block.items()))


def _result_object(content: str) -> dict[str, Any] | None:
    """Return the vendor result object carrying a modelUsage block, or None.

    Whole-file JSON first (``--output-format json``), then the last stream-json
    line that carries a block — earlier lines hold partial totals.
    """

    try:
        whole = json.loads(content)
    except ValueError:  # JSONDecodeError is a ValueError subclass
        whole = None

    if isinstance(whole, dict) and isinstance(whole.get("modelUsage"), dict):
        return whole

    found: dict[str, Any] | None = None
    for line in content.splitlines():
        line = line.strip()
        if not line.startswith("{"):
            continue
        try:
            obj = json.loads(line)
        except ValueError:  # JSONDecodeError is a ValueError subclass
            continue
        if isinstance(obj, dict) and isinstance(obj.get("modelUsage"), dict):
            found = obj

    return found


def parse_model_usage(stdout_path: Path) -> VendorUsage | None:
    """Read agent_stdout.log and return the vendor's own usage and bottom line.

    Returns None when the run carries no block this module is willing to bill
    from — the caller's signal to fall back to trace derivation, which is worse
    but is disclosed in the report. Every rejection is logged with its reason.

    The vendor writes its bottom line as ``total_cost_usd`` beside the block, and
    that total — not a re-summation of the parts — is what gets billed. Re-summing
    would be one more local re-derivation of a number the vendor already computed,
    which is the whole bug this module was rebuilt to stop committing.

    The per-model records still have to reconcile with that total, and a block
    that does not is rejected rather than billed: a block missing a model agrees
    with a total that also omits it, so a quiet undercount is exactly what a
    "close enough" sum would buy. A block with no total cannot be checked at all,
    so it is rejected too; the writer emits the total beside every block.
    """

    if not stdout_path.exists():
        return None

    try:
        content = stdout_path.read_text()
    except OSError:
        logger.warning("Failed to read %s", stdout_path)
        return None

    if not content.strip():
        return None

    result = _result_object(content)
    if result is None:
        return None

    block = result["modelUsage"]
    if not block:
        return None

    try:
        records = _model_usage_records(block)
        vendor_total = _finite(result.get("total_cost_usd"), "<run>", "total_cost_usd")
    except ValueError as exc:
        logger.warning(
            "%s: unusable modelUsage block (%s) — falling back to trace derivation",
            stdout_path,
            exc,
        )
        return None

    summed = sum(r.cost_usd for r in records)
    tolerance = max(COST_RECONCILE_ABS, COST_RECONCILE_REL * abs(vendor_total))
    if abs(summed - vendor_total) > tolerance:
        logger.warning(
            "%s: per-model costUSD sums to %.6f but the vendor reports "
            "total_cost_usd=%.6f — the block is incomplete, so it is rejected "
            "rather than billed short. Falling back to trace derivation.",
            stdout_path,
            summed,
            vendor_total,
        )
        return None

    return VendorUsage(models=records, total_cost_usd=vendor_total)


def merge_model_usage(models: tuple[ModelUsage, ...], num_requests: int) -> Usage:
    """Fold per-model vendor records into one run-level Usage.

    ``model`` is the largest spender, so the representative label survives the
    haiku sub-agent calls that ride along with most runs.

    Requires at least one record: folding zero would yield a zero-cost Usage that
    still looked vendor-authoritative.
    """

    if not models:
        raise ValueError("merge_model_usage requires at least one ModelUsage record")

    primary = max(models, key=lambda m: (m.cost_usd, m.output_tokens))
    return Usage(
        input_tokens=sum(m.input_tokens for m in models),
        output_tokens=sum(m.output_tokens for m in models),
        cache_write_tokens=sum(m.cache_write_tokens for m in models),
        cache_read_tokens=sum(m.cache_read_tokens for m in models),
        model=primary.model,
        num_requests=num_requests,
    )


def compute_cost(usage: Usage, model: str | None = None) -> float:
    """Return USD cost for a Usage given the local PRICING table (tier 2 only)."""

    resolved_model = model or usage.model or DEFAULT_MODEL
    prices = PRICING.get(resolved_model)
    if prices is None:
        # Debug, not warning: this runs on every task to produce the
        # reconciliation baseline, including the vendor-priced ones whose cost
        # never comes from this table. aggregate_report raises the real alarm,
        # scoped to the tasks PRICING actually billed.
        logger.debug(
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

            # Derive from the trace unconditionally: it is the tier-2 cost when
            # there is no vendor block, and the reconciliation baseline when
            # there is.
            trace_usage = parse_trace(trace_path)
            trace_cost = compute_cost(trace_usage)

            vendor = parse_model_usage(task_dir / "agent_stdout.log")
            if vendor:
                usage = merge_model_usage(vendor.models, trace_usage.num_requests)
                cost = round(vendor.total_cost_usd, 6)
                cost_source = "sdk"
                models = tuple(m.model for m in vendor.models)
            else:
                usage = trace_usage
                cost = trace_cost
                cost_source = "trace"
                models = (trace_usage.model,)

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
                    cost_source=cost_source,
                    trace_cost_usd=trace_cost,
                    models=models,
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


def _cost_source_summary(costs: list[TaskCost]) -> dict[str, Any]:
    """Report where cost came from, and how far the trace derivation missed.

    Two things a reader of this JSON must not have to guess: how many runs are
    still trace-derived (those carry the old distortion and are not mixed in
    silently), and by how much the trace derivation disagrees with the vendor on
    the runs where both exist. The ratio sizes the bug this module was rebuilt to
    fix, so a regression shows up as a number rather than a quietly wrong total.
    """

    sdk = [tc for tc in costs if tc.cost_source == "sdk"]
    trace = [tc for tc in costs if tc.cost_source == "trace"]

    vendor_total = round(sum(tc.cost_usd for tc in sdk), 6)
    derived_total = round(sum(tc.trace_cost_usd for tc in sdk), 6)

    return {
        "sdk": len(sdk),
        "trace": len(trace),
        "trace_derived_task_ids": sorted(f"{tc.task_id}:{tc.mode}" for tc in trace),
        "reconciliation": {
            "vendor_cost_usd": vendor_total,
            "trace_derived_cost_usd": derived_total,
            "delta_usd": round(vendor_total - derived_total, 6),
            "trace_over_vendor_ratio": (
                round(derived_total / vendor_total, 4) if vendor_total else 0.0
            ),
        },
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
            "models": list(tc.models),
            "input_tokens": tc.usage.input_tokens,
            "output_tokens": tc.usage.output_tokens,
            "cache_write_tokens": tc.usage.cache_write_tokens,
            "cache_read_tokens": tc.usage.cache_read_tokens,
            "num_requests": tc.usage.num_requests,
            "cost_usd": tc.cost_usd,
            "cost_source": tc.cost_source,
            "trace_cost_usd": tc.trace_cost_usd,
            "agent_duration_seconds": tc.agent_duration_seconds,
        }
        for tc in sorted(costs, key=lambda c: c.task_id)
    ]

    # Only the trace-derived population is exposed to PRICING, so only it can be
    # mispriced. An unpriced model here is billed at DEFAULT_MODEL rates, and such
    # models cluster in a single arm, so the substitution skews arm-to-arm deltas
    # rather than just absolute cost. The caveat rides in the report — a warning
    # in a batch log does not reach whoever reads the JSON.
    unpriced_models = sorted(
        {
            model
            for tc in costs
            if tc.cost_source == "trace"
            for model in tc.models
            if model not in PRICING
        }
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
        "cost_sources": _cost_source_summary(costs),
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
