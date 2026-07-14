#!/usr/bin/env python3
"""
cost_tracker.py — Report per-task / aggregate costs for EnterpriseBench runs.

Cost has two sources, in order:

tier 1 ("sdk")    Claude Code's per-model ``modelUsage`` block in agent_stdout.log,
                  carrying the vendor's own ``costUSD`` per model. Authoritative.
tier 2 ("trace")  Re-derived from agent_trace.jsonl against the local PRICING
                  table, only when a run has no usable vendor block.

Tier 2 is lossy and cannot be made otherwise: the trace records a single model per
run and carries no sub-agent usage, so a multi-model run cannot be priced correctly
from it however carefully it is summed. Every tier-2 run is named in the report.

Both numbers are computed for every run and their disagreement is published, per
bucket as well as overall — the distortion this module exists to catch is
per-arm, so a blended ratio alone would hide it.
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
# Pricing — per million tokens. Tier-2 runs only; tier-1 runs are billed with the
# vendor's costUSD, which prices models this table has never heard of.
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

# Checksum slack between the per-model costUSD records and the vendor's own
# total_cost_usd: an absolute floor for float summation, a relative term for a
# vendor that rounds its total.
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

    Tokens come from the vendor block on a tier-1 run and from the trace on a
    tier-2 one. ``model`` is a representative label only — the largest spender on
    tier 1, the sole model the trace named on tier 2; :attr:`TaskCost.models` has
    the full set. ``num_requests`` is always trace-derived; the vendor block
    reports no request count.
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

    ``vendor`` is the tier: present means the run is billed from the vendor's own
    total, absent means it falls back to the trace derivation. Cost, source and
    model list all follow from it, so they are properties rather than stored
    fields — a record that claims one tier and carries the other's numbers is not
    constructible.

    ``trace_cost_usd`` is what the trace derivation bills, kept for every run as
    the reconciliation baseline; on a tier-2 run it is also the billed cost.
    """

    task_id: str
    mode: str
    suite: str
    difficulty: str
    usage: Usage
    trace_cost_usd: float
    vendor: VendorUsage | None
    agent_duration_seconds: float

    @property
    def cost_usd(self) -> float:
        if self.vendor is None:
            return self.trace_cost_usd
        return round(self.vendor.total_cost_usd, 6)

    @property
    def cost_source(self) -> str:
        return "trace" if self.vendor is None else "sdk"

    @property
    def models(self) -> tuple[str, ...]:
        if self.vendor is None:
            return (self.usage.model,)
        return tuple(m.model for m in self.vendor.models)


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

    ``json.loads`` accepts the non-standard literals ``NaN`` / ``Infinity``. NaN
    has to be rejected here because it is quiet: every comparison against it is
    False, so it would pass the checksum below untouched and then turn the whole
    report's total into NaN.
    """

    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError(f"model {model!r} field {field} is {value!r}, not a number")
    if not math.isfinite(value):
        raise ValueError(f"model {model!r} field {field} is non-finite ({value!r})")
    return float(value)


def _count(value: Any, model: str, field: str) -> int:
    """Return a token count as an int. Absent means zero; unreadable raises.

    :func:`_finite` must run before the ``int()``: ``int(float("inf"))`` raises
    OverflowError, which the caller only handles ValueError for.
    """

    if value is None:
        return 0
    return int(_finite(value, model, field))


def _model_usage_records(block: dict[str, Any]) -> tuple[ModelUsage, ...]:
    """Convert a per-model modelUsage mapping into records.

    Strict by design: raises ValueError on an entry it cannot read whole rather
    than skipping it, because a skipped model still bills the run, just for less.
    The caller turns that into a disclosed fallback.
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

    return tuple(record(name, entry) for name, entry in sorted(block.items()))


def _result_object(content: str) -> dict[str, Any] | None:
    """Return the vendor result object carrying a modelUsage block, or None.

    Whole-file JSON first (``--output-format json``, which can be pretty-printed
    and so is invisible to a line scan), then the LAST stream-json line carrying a
    block — earlier lines hold partial totals, so last wins.

    The scan runs backwards and returns on the first hit, and only parses lines
    that mention the key: these logs run to megabytes, and JSON-decoding every
    line of every one of them to find a block that lives on the last line was the
    dominant cost of the whole tool.
    """

    try:
        whole = json.loads(content)
    except ValueError:  # JSONDecodeError is a ValueError subclass
        whole = None

    if isinstance(whole, dict) and isinstance(whole.get("modelUsage"), dict):
        return whole

    for line in reversed(content.splitlines()):
        line = line.strip()
        if not line.startswith("{") or '"modelUsage"' not in line:
            continue
        try:
            obj = json.loads(line)
        except ValueError:  # JSONDecodeError is a ValueError subclass
            continue
        if isinstance(obj, dict) and isinstance(obj.get("modelUsage"), dict):
            return obj

    return None


def parse_model_usage(stdout_path: Path) -> VendorUsage | None:
    """Read agent_stdout.log and return the vendor's own usage and bottom line.

    Returns None when the run carries no block this module is willing to bill from
    — the caller's signal to fall back to trace derivation. Every rejection is
    logged with its reason.

    What gets billed is the vendor's ``total_cost_usd``, not a re-sum of the parts.
    The parts must still reconcile with it, and a block that fails to is rejected
    rather than billed: a block missing a model agrees with a total that also omits
    it, so a "close enough" sum buys a quiet undercount. A block with no usable
    total cannot be checked at all, so it is rejected too.
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

    Rejects an empty tuple: folding zero records would yield a zero-cost Usage
    that still looked vendor-authoritative.
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


def compute_cost(usage: Usage) -> float:
    """Return USD cost for a Usage given the local PRICING table (tier 2 only)."""

    resolved_model = usage.model or DEFAULT_MODEL
    prices = PRICING.get(resolved_model)
    if prices is None:
        # Debug, not warning: this runs on every task to build the reconciliation
        # baseline, vendor-priced ones included. aggregate_report raises the real
        # alarm, scoped to the tasks PRICING actually billed.
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

            # The trace is parsed unconditionally: it is the tier-2 cost when there
            # is no vendor block, the reconciliation baseline when there is, and
            # the only source of num_requests either way.
            trace_usage = parse_trace(trace_path)
            trace_cost = compute_cost(trace_usage)

            vendor = parse_model_usage(task_dir / "agent_stdout.log")
            usage = (
                merge_model_usage(vendor.models, trace_usage.num_requests)
                if vendor
                else trace_usage
            )

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
                    trace_cost_usd=trace_cost,
                    vendor=vendor,
                    agent_duration_seconds=duration,
                )
            )

    return costs


# ---------------------------------------------------------------------------
# Aggregation
# ---------------------------------------------------------------------------


def _reconcile(items: list[TaskCost]) -> dict[str, Any]:
    """Compare the billed vendor cost against what the trace would have derived.

    Scoped to the tier-1 runs, the only ones where both numbers exist (on a tier-2
    run they are equal by construction and the ratio would be a meaningless 1.0).

    Reported per bucket as well as overall, because the distortion is not uniform:
    the trace under-derives by a different factor in each arm (0.375 / 0.534 /
    0.859 when this was measured), which is what corrupts the arm-to-arm deltas
    the benchmark exists to produce. A single blended ratio can sit still while
    the arms move apart underneath it.
    """

    sdk = [tc for tc in items if tc.vendor is not None]
    vendor_total = round(sum(tc.cost_usd for tc in sdk), 6)
    derived_total = round(sum(tc.trace_cost_usd for tc in sdk), 6)

    return {
        "vendor_cost_usd": vendor_total,
        "trace_derived_cost_usd": derived_total,
        "delta_usd": round(vendor_total - derived_total, 6),
        "trace_over_vendor_ratio": (
            round(derived_total / vendor_total, 4) if vendor_total else 0.0
        ),
    }


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
        "reconciliation": _reconcile(items),
    }


def _cost_source_summary(costs: list[TaskCost]) -> dict[str, Any]:
    """Report where cost came from, and how far the trace derivation missed.

    Names every trace-derived run rather than counting it: those carry the old
    distortion, and a reader must not have to guess which rows are mixed in.
    """

    trace = [tc for tc in costs if tc.vendor is None]

    return {
        "sdk": len(costs) - len(trace),
        "trace": len(trace),
        "trace_derived_task_ids": sorted(f"{tc.task_id}:{tc.mode}" for tc in trace),
        "reconciliation": _reconcile(costs),
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
            tc.usage.model
            for tc in costs
            if tc.vendor is None and tc.usage.model not in PRICING
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
        # allow_nan=False: a NaN/Infinity that slipped past the upstream _finite
        # guards must raise here, not emit non-standard JSON a downstream reader
        # would choke on. Enforces at the serialization boundary the same
        # strict-JSON guarantee the tests already assert.
        json.dump(report, fh, indent=2, allow_nan=False)
    logger.info("Report written to %s", output_path)


if __name__ == "__main__":
    main()
