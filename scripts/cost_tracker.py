#!/usr/bin/env python3
"""
cost_tracker.py — Report per-task / aggregate costs for EnterpriseBench runs.

The report publishes TWO cost views and never blends them into one number
(EnterpriseBench-jrgs). A task re-run across batches produces one *attempt* per
run, and the two views answer different questions about those attempts:

``operational_economics``  total spend, summed over every attempt, including
                           re-runs and attempts the scoring layer never scored.
                           This is what was actually paid.
``comparison_economics``   one selected attempt per (task_id, mode), restricted
                           to tasks present in every arm. This is the only view
                           an arm-to-arm cost claim may be built on.

No single total is published: one number is correct as spend and wrong as suite
cost, and nothing in the JSON would say which. Nor is any average taken over
attempts — that weights a re-run-heavy arm differently from a clean one, which
corrupts exactly the arm-to-arm delta the benchmark exists to produce.

Every count in the comparison view counts distinct task_ids — in the headline
and in all three breakdowns — and the list of (task_id, mode) rows is named
``per_cell``. A cell count published under the name ``tasks`` reads as a task
count, so a four-arm sweep would state its size at four times the truth, in
report prose and baked into chart titles.

The selecting rule is deliberately the one ``analyze_scores`` already uses to
collapse (task_id, mode) — highest normalized score. If the two modules chose
differently, the cost row and the score row of the same cell would describe
different runs, and every score-vs-cost join would be wrong.

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
import sys
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from datetime import datetime, timezone
from operator import attrgetter
from pathlib import Path
from typing import Any

from analyze_scores import parse_result
from lib.shared import (
    VALID_MODES,
    load_task_index,
    split_variant_label,
    strip_mode_suffix,
)

PROJECT_ROOT = Path(__file__).resolve().parent.parent

# Bootstrap `lib/` the same way analyze_scores.py does. Importing it above
# happens to do this as a side effect, but relying on that makes the eb_verify
# import below silently ordering-dependent: any isort/ruff reorder that lifts
# `eb_verify` above `analyze_scores` breaks `make cost` on a checkout without
# an editable install. Doing it explicitly costs three lines and removes the
# ordering as a correctness condition.
if str(PROJECT_ROOT / "lib") not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT / "lib"))

from eb_verify.score_contract import (  # noqa: E402
    ScoreContractError,
    format_unreadable_sample,
)

logger = logging.getLogger(__name__)

# Bumped whenever the report's shape changes. Consumers call require_schema and
# refuse an unknown version rather than reading a missing key through
# ``.get(key, 0)`` and rendering a plausible $0.00 — the failure class
# lib/eb_verify/scorer_guard.py exists to forbid, applied to the cost artifact.
SCHEMA_VERSION = 4

# How ``select_attempt`` picks the one attempt that represents a (task_id, mode)
# cell. Published in the report so a reader never has to infer it.
SELECTION_RULE = (
    "highest normalized_score, then latest trace timestamp, then run_dir; "
    "attempts with no score are never selected. Matches analyze_scores."
)

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
    # fable-5 / opus-4-8 rates recovered from the vendor's own per-model costUSD
    # in tier-1 modelUsage blocks (implied input rate is exact, zero variance),
    # under Anthropic's standard multipliers (output 5x, cache_write 1.25x,
    # cache_read 0.1x input). Tier-2 runs of either are rare today — both cluster
    # in vendor-priced condB — so these mainly keep the reconciliation baseline
    # honest (EnterpriseBench-qjfi).
    "claude-fable-5": {
        "input": 10.0,
        "output": 50.0,
        "cache_write": 12.5,
        "cache_read": 1.0,
    },
    "claude-opus-4-8": {
        "input": 5.0,
        "output": 25.0,
        "cache_write": 6.25,
        "cache_read": 0.5,
    },
}

DEFAULT_MODEL = "claude-sonnet-4-6"

# Claude Code stamps this on an ``isApiErrorMessage`` assistant line (a 401/429
# placeholder) in place of a real model name. It is a sentinel, not a model, so
# ``scan_trace`` refuses to let it latch a run's representative model: a
# pure-error run then resolves to DEFAULT_MODEL at zero usage (zero cost), and a
# retried run keeps the real model from its successful turn. Guarding at capture
# is what keeps such tokens from being mispriced and dropped from the
# ``unpriced_models`` signal (EnterpriseBench-qjfi).
SYNTHETIC_MODEL = "<synthetic>"

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
class TraceScan:
    """One pass over an agent_trace.jsonl: its usage and when it ran.

    The timestamp comes out of the same pass rather than a second one — the
    traces run to thousands of lines and are already fully decoded here.
    """

    usage: Usage
    last_timestamp: str


@dataclass(frozen=True)
class TaskCost:
    """One *attempt* — a single run of one task in one mode.

    Two runs of the same (task_id, mode) are two records, distinguished by
    ``run_dir``. They used to be indistinguishable, which is what let re-runs
    inflate the batch total silently.

    ``normalized_score`` is None when the scoring layer produced no score for
    this attempt. Such an attempt still spent money (it belongs to the
    operational view) but cannot represent its cell (it is never selected for
    the comparison view). ``trace_timestamp`` is read from the trace itself, so
    it survives a clone or a rescore pass; file mtime does not.

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
    run_dir: str
    normalized_score: float | None
    trace_timestamp: str = ""
    variant_label: str | None = None

    @property
    def cell(self) -> tuple[str, str]:
        """The (task_id, mode) cell this attempt belongs to."""

        return (self.task_id, self.mode)

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


def scan_trace(trace_path: Path) -> TraceScan:
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

    ``last_timestamp`` is the largest ISO-8601 ``timestamp`` on any line of the
    trace, assistant or not (the first line of a real trace is a
    ``queue-operation``). It orders re-runs of the same cell without reference
    to file mtime, which no copy, clone or rescore pass preserves.
    """

    # request key -> that request's most complete usage snapshot. A line with no
    # request key of its own is keyed by line number (an int, so it can never
    # collide with a requestId) and therefore billed exactly once — correct for
    # legacy single-block traces, where each line already is its own request.
    selected: dict[str | int, dict[str, Any]] = {}
    model = ""
    ungrouped_lines = 0
    last_timestamp = ""

    with trace_path.open() as fh:
        for line_no, line in enumerate(fh):
            line = line.strip()
            if not line:
                continue
            try:
                entry = json.loads(line)
            except (ValueError, RecursionError):
                # JSONDecodeError is a ValueError subclass. A 4300-digit int
                # literal raises a bare ValueError (CPython int-str guard) and a
                # deeply-nested line raises RecursionError; both are malformed
                # input to skip, not a reason to abort the batch.
                logger.warning("Skipping malformed line in %s", trace_path)
                continue

            # Read before the assistant filter: the timestamp rides on every
            # line type, and the earliest lines of a trace are not assistant.
            stamp = entry.get("timestamp")
            if isinstance(stamp, str) and stamp > last_timestamp:
                last_timestamp = stamp

            if entry.get("type") != "assistant":
                continue

            msg = entry.get("message", {})
            if not msg:
                continue

            # Capture the model from the first assistant message that names a
            # real one, skipping isApiErrorMessage / ``<synthetic>`` placeholder
            # lines so they can't latch the run's representative model. See
            # SYNTHETIC_MODEL for why (EnterpriseBench-qjfi).
            msg_model = msg.get("model", "")
            if (
                msg_model
                and not model
                and not entry.get("isApiErrorMessage")
                and msg_model != SYNTHETIC_MODEL
            ):
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

    return TraceScan(
        usage=Usage(
            input_tokens=total("input_tokens"),
            output_tokens=total("output_tokens"),
            cache_write_tokens=total("cache_creation_input_tokens"),
            cache_read_tokens=total("cache_read_input_tokens"),
            model=model or DEFAULT_MODEL,
            num_requests=len(selected),
        ),
        last_timestamp=last_timestamp,
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


def _parse_dir_identity(dir_path: Path) -> tuple[str, str, str | None]:
    """Infer (task_id, mode, variant_label) from a results directory path.

    - results/runs/<task_id>/<mode>--<label>/ -> labeled multi-mode layout
    - results/runs/<task_id>/<mode>/           -> multi-mode layout
    - results/runs/<task_id>/                  -> baseline (legacy)
    - results/mcp_batch*/<id>_<mode>/          -> parse mode from suffix
    """

    name = dir_path.name
    parent_name = dir_path.parent.name
    grandparent_name = dir_path.parent.parent.name if dir_path.parent.parent else ""

    # Multi-mode layout: results/runs/<task_id>/<mode>[--<label>]/
    mode, variant_label = split_variant_label(name)
    if mode in VALID_MODES and grandparent_name == "runs":
        return parent_name, mode, variant_label

    # Legacy single-mode: results/runs/<task_id>/
    if parent_name == "runs":
        return name, "baseline", None

    task_id, mode = strip_mode_suffix(name)
    # strip_mode_suffix defaults to "baseline" when no suffix found;
    # for non-runs directories without a suffix, treat as "unknown".
    if mode == "baseline" and not name.endswith("_baseline"):
        return name, "unknown", None
    return task_id, mode, None


def _attempt_score(
    task_dir: Path, benchmarks_root: Path, *, allow_legacy: bool = False
) -> float | None:
    """Return this attempt's normalized score, or None if it has none.

    Delegates to ``analyze_scores.parse_result`` rather than reading
    ``task_score`` locally. The coupling is the point: cost selection has to
    collapse a (task_id, mode) cell to the same attempt the score pipeline
    does, and a private read would drift apart silently — including from the
    contract check that decides what ``task_score`` even means
    (``eb_verify.score_contract``).

    None means the scoring layer produced nothing for this run — no results.json,
    no scores block, or zero checkpoints. That is a real distinction, not a zero:
    the attempt still spent money, it just cannot stand for its cell.
    """

    results_path = task_dir / "results.json"
    if not results_path.exists():
        return None

    result = parse_result(results_path, benchmarks_root, allow_legacy=allow_legacy)
    return None if result is None else result.normalized_score


def _run_dir_label(task_dir: Path, root: Path) -> str:
    """Identify an attempt's directory, relative to *root* when it is under it.

    cost_report.json is a tracked artifact, so an absolute path would commit one
    machine's home directory and make the report non-portable — and ``run_dir``
    is a tiebreak key, so it has to read the same on every checkout. Falls back
    to the absolute path for a directory outside *root*, which is still correct,
    just not portable; there is nothing shorter to say about it.
    """

    try:
        return str(task_dir.resolve().relative_to(root.resolve()))
    except ValueError:
        return str(task_dir)


def scan_results_dirs(
    dirs: list[Path],
    benchmarks_root: Path,
    root: Path = PROJECT_ROOT,
    *,
    allow_legacy: bool = False,
) -> list[TaskCost]:
    """Find every result directory with an agent_trace.jsonl and cost its attempt.

    One record per attempt, not per cell: a task re-run in several batches
    yields several records that differ only in ``run_dir``. Collapsing them is
    :func:`select_attempt`'s job, and only the comparison view asks for it.

    *root* is the project root that ``run_dir`` is reported relative to. It
    defaults to this checkout rather than to None: ``run_dir`` is both a
    published field and a tiebreak key, so leaving it absolute has to be a
    caller's deliberate choice, not what happens when nobody says.
    """

    costs: list[TaskCost] = []
    unreadable: list[str] = []

    for root_dir in dirs:
        if not root_dir.is_dir():
            logger.info("Skipping missing directory: %s", root_dir)
            continue

        for trace_path in sorted(root_dir.rglob("agent_trace.jsonl")):
            task_dir = trace_path.parent
            task_id, mode, variant_label = _parse_dir_identity(task_dir)
            meta = _get_task_meta(task_id, benchmarks_root)

            # The trace is parsed unconditionally: it is the tier-2 cost when there
            # is no vendor block, the reconciliation baseline when there is, and
            # the only source of num_requests either way.
            scan = scan_trace(trace_path)
            trace_usage = scan.usage
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

            # Same fail-closed rule as analyze_scores, and for the same
            # reason: the two modules must collapse a (task_id, mode) cell to
            # the SAME attempt, which they cannot do if one of them silently
            # drops the attempts the other refuses. Collected and raised once
            # after the scan so the operator learns how much of the corpus is
            # affected, not just which file came first.
            try:
                score = _attempt_score(
                    task_dir, benchmarks_root, allow_legacy=allow_legacy
                )
            except ScoreContractError:
                unreadable.append(str(task_dir))
                continue

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
                    run_dir=_run_dir_label(task_dir, root),
                    normalized_score=score,
                    trace_timestamp=scan.last_timestamp,
                    variant_label=variant_label,
                )
            )

    if unreadable:
        raise ScoreContractError(
            f"{len(unreadable)} attempt(s) declare no score contract, so what "
            f"their task_score means is unknown and neither reading is "
            f"assumed:\n"
            + format_unreadable_sample(unreadable)
            + "\n\nNo cost report was written. Re-run those tasks, point "
            "--results-dir at only the current corpus, or pass "
            "--legacy-score-contract."
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


def _distinct_tasks(items: list[TaskCost]) -> int:
    return len({t.task_id for t in items})


@dataclass(frozen=True)
class Denominator:
    """What a bucket divides by, and the key names that say so out loud.

    The denominator is spelled into both key names, so a reader who reaches for
    an average the enclosing block does not publish gets a KeyError rather than
    a plausible number over the wrong divisor. Carrying the counting rule here
    rather than branching on a bare string means a new denominator cannot be
    introduced without declaring how it counts.
    """

    count_key: str
    avg_key: str
    count: Callable[[list[TaskCost]], int]


#: The operational view bills every attempt, because every attempt was paid for.
ATTEMPTS = Denominator("attempts", "avg_cost_per_attempt", len)

#: Within one arm a (task_id, mode) cell IS a task, so this is what one task
#: cost in that arm — the only average an arm-to-arm claim may use. Dividing by
#: attempts instead weights a re-run-heavy arm differently from a clean one.
TASKS_IN_ARM = Denominator("tasks", "avg_cost_per_task", _distinct_tasks)

#: A suite or difficulty slice spans every arm, so its total sums the arms and
#: its average is what one task cost across the whole sweep. That answers a
#: different question than TASKS_IN_ARM, so it does not reuse the key: sharing
#: it would put a field's meaning back inside its enclosing block.
TASKS_ACROSS_ARMS = Denominator(
    "tasks", "avg_cost_per_task_across_arms", _distinct_tasks
)


def _bucket(items: list[TaskCost], unit: Denominator, reconcile: bool) -> dict[str, Any]:
    """Stats for one slice of a view, denominated per *unit*.

    One function rather than one per view keeps the four shared fields from
    drifting apart, which is the same failure this whole report exists to
    prevent.

    *reconcile* is operational-only: the comparison view bills one attempt per
    cell, so a vendor-vs-trace ratio over it describes a sample, not the spend.
    """

    count = unit.count(items)
    total_cost = round(sum(t.cost_usd for t in items), 6)
    bucket = {
        unit.count_key: count,
        "total_cost_usd": total_cost,
        unit.avg_key: round(total_cost / count, 6) if count else 0.0,
        "total_input_tokens": sum(t.usage.input_tokens for t in items),
        "total_output_tokens": sum(t.usage.output_tokens for t in items),
    }
    if reconcile:
        bucket["reconciliation"] = _reconcile(items)
    return bucket


def _buckets(
    items: Sequence[TaskCost],
    in_arm: Denominator,
    across_arms: Denominator,
    reconcile: bool,
) -> dict[str, Any]:
    """The three dimensional breakdowns of a view.

    ``by_mode`` slices within one arm; ``by_suite`` and ``by_difficulty`` slice
    across all of them. The operational view passes the same denominator twice
    because an attempt is an attempt in either direction.

    The dimensions are a table rather than a branch on the dimension name, so a
    fourth breakdown has to declare its denominator instead of inheriting one.
    """

    dims = (("mode", in_arm), ("suite", across_arms), ("difficulty", across_arms))
    return {
        f"by_{dim}": {
            k: _bucket(v, unit, reconcile)
            for k, v in sorted(_group_by(items, attrgetter(dim)).items())
        }
        for dim, unit in dims
    }


def _group_by(items: Sequence[TaskCost], key: Any) -> dict[Any, list[TaskCost]]:
    grouped: dict[Any, list[TaskCost]] = {}
    for tc in items:
        grouped.setdefault(key(tc), []).append(tc)
    return grouped


def select_attempt(attempts: list[TaskCost]) -> TaskCost | None:
    """Pick the one attempt that represents a (task_id, mode) cell.

    Highest normalized score, ties broken by latest trace timestamp and then by
    ``run_dir``. Returns None when no attempt in the cell was scored.

    The primary key is ``analyze_scores``' — that module already collapses
    (task_id, mode) by highest score, and a different rule here would pair each
    cell's cost with a *different* run than its score, silently breaking every
    score-vs-cost join downstream. ``TestAgreesWithAnalyzeScores`` pins that.

    Both tiebreakers are content-derived and total, so the result does not depend
    on scan order, filesystem order, or mtime. ``analyze_scores`` has no
    tiebreaker at all — it compares with a strict ``>`` and so keeps whichever
    equally-scored run ``rglob`` reached first — so on an exact score tie the two
    modules can still pick different runs. The score is identical either way; the
    cost need not be. Fixed separately in EnterpriseBench-ye0tp; do not "fix" it
    here by dropping the tiebreakers, which would make this side non-deterministic
    too rather than making both sides agree.
    """

    scored = [a for a in attempts if a.normalized_score is not None]
    if not scored:
        return None
    return max(scored, key=lambda a: (a.normalized_score, a.trace_timestamp, a.run_dir))


@dataclass(frozen=True)
class ComparisonSet:
    """The matched comparison population and every count the report makes of it.

    One object rather than a tuple the caller re-derives fields from. ``rows``
    is one attempt per (task_id, mode) CELL, so ``len(rows)`` is
    ``len(task_ids) * len(modes)``, not a task count. The two are separate
    fields so no caller can measure one by taking the length of the other.

    Tuples, not lists: ``frozen=True`` stops a field being rebound but not a
    list being appended to, and these four have to agree with each other. A
    caller that grew ``rows`` would move ``len(rows)`` off ``tasks x modes``
    and re-open the counting ambiguity from the inside.
    """

    rows: tuple[TaskCost, ...]
    task_ids: tuple[str, ...]
    modes: tuple[str, ...]
    excluded_task_ids: tuple[str, ...]


def comparison_attempts(costs: list[TaskCost]) -> ComparisonSet:
    """Select the matched, comparable attempt set out of every attempt made.

    Two restrictions, in order:

    1. One attempt per (task_id, mode), via :func:`select_attempt`. Unscored
       attempts drop out here; a cell with only unscored attempts has no row.
    2. Only tasks present in *every* arm survive. Per-arm totals over different
       task sets are not comparable, and rendering them side by side (which the
       charts do) invites exactly the conclusion the data cannot support.

    The arm set comes from every in-scope attempt, scored or not. Deriving it
    from the selected cells instead would let an arm that scored nothing — a
    verifier or infra outage across one whole arm — silently leave the
    intersection rather than empty it. An arm that ran must keep its seat: with
    it, nothing matches and every task is named as excluded, which is a
    diagnosable report. Without it, the remaining arms match perfectly and
    nothing says an arm is gone.

    Modes outside :data:`VALID_MODES` — an unparseable directory name resolving
    to "unknown" — define no arm and are operational-only. Letting one in would
    collapse the intersection to nothing.

    A task no arm ever scored never enters the matching at all, so it is absent
    here rather than listed as excluded — whether it ran in every arm or in one.
    "Unmatched" says some arm scored it and another did not, which is false in
    either shape. ``invalid_attempts`` enumerates it, with its run_dir and cost.
    """

    in_scope = [
        tc
        for tc in costs
        if tc.mode in VALID_MODES and tc.variant_label is None
    ]
    cells = _group_by(in_scope, attrgetter("cell"))

    selected = {cell: pick for cell, items in cells.items()
                if (pick := select_attempt(items)) is not None}

    modes = {tc.mode for tc in in_scope}
    arms_per_task: dict[str, set[str]] = {}
    for task_id, mode in selected:
        arms_per_task.setdefault(task_id, set()).add(mode)

    matched = {tid for tid, arms in arms_per_task.items() if arms == modes}
    return ComparisonSet(
        rows=tuple(tc for cell, tc in sorted(selected.items()) if cell[0] in matched),
        task_ids=tuple(sorted(matched)),
        modes=tuple(sorted(modes)),
        excluded_task_ids=tuple(sorted(set(arms_per_task) - matched)),
    )


def _duplicate_attempts(costs: list[TaskCost]) -> list[dict[str, Any]]:
    """Enumerate every re-run cell, naming which attempt the comparison view took.

    The policy requires retry attempts to be excluded from ratios *and* fully
    enumerated. A count alone would leave a reader unable to check the selection.
    """

    listing: list[dict[str, Any]] = []
    for (task_id, mode), items in sorted(_group_by(costs, attrgetter("cell")).items()):
        if len(items) < 2:
            continue
        chosen = select_attempt(items)
        listing.append(
            {
                "task_id": task_id,
                "mode": mode,
                "attempts": len(items),
                "runs": [
                    {
                        "run_dir": tc.run_dir,
                        "cost_usd": tc.cost_usd,
                        "normalized_score": tc.normalized_score,
                        "trace_timestamp": tc.trace_timestamp,
                        "selected": tc is chosen,
                    }
                    for tc in sorted(items, key=lambda t: t.run_dir)
                ],
            }
        )
    return listing


def _invalid_attempts(ordered: list[TaskCost]) -> list[dict[str, Any]]:
    """Enumerate attempts the scoring layer never scored. They are spend, not zero."""

    return [
        {
            "task_id": tc.task_id,
            "mode": tc.mode,
            "run_dir": tc.run_dir,
            "cost_usd": tc.cost_usd,
            "reason": "no_normalized_score",
        }
        for tc in ordered
        if tc.normalized_score is None
    ]


def _cost_source_summary(costs: list[TaskCost]) -> dict[str, Any]:
    """Report where cost came from, and how far the trace derivation missed.

    Names every trace-derived attempt rather than counting it: those carry the
    old distortion, and a reader must not have to guess which rows are mixed in.
    The key is the run directory, not ``task:mode`` — one attempt of a re-run
    cell can be trace-derived while another is vendor-priced, and a cell-level
    key would misattribute the caveat to both.
    """

    trace = [tc for tc in costs if tc.vendor is None]

    return {
        "sdk": len(costs) - len(trace),
        "trace": len(trace),
        "trace_derived_attempts": sorted(
            f"{tc.task_id}:{tc.mode}@{tc.run_dir}" for tc in trace
        ),
        "reconciliation": _reconcile(costs),
    }


def _attempt_row(tc: TaskCost) -> dict[str, Any]:
    return {
        "task_id": tc.task_id,
        "mode": tc.mode,
        "variant_label": tc.variant_label,
        "suite": tc.suite,
        "difficulty": tc.difficulty,
        "run_dir": tc.run_dir,
        "normalized_score": tc.normalized_score,
        "trace_timestamp": tc.trace_timestamp,
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


def require_schema(report: dict[str, Any], consumer: str) -> None:
    """Raise unless *report* is a cost report this consumer can read.

    Every consumer reads cost fields through ``.get(key, default)``. Feed a
    report written by another schema to such a reader and it renders a plausible
    $0.00 instead of failing — a fabricated number, which is precisely what the
    scoring trust boundary forbids. So the version is checked, not defaulted.
    """

    version = report.get("schema_version")
    if version != SCHEMA_VERSION:
        raise ValueError(
            f"{consumer}: cost report declares schema_version={version!r}, "
            f"expected {SCHEMA_VERSION}. Regenerate it with "
            f"`python3 scripts/cost_tracker.py` — reading it as-is would print "
            f"zeros for every missing field."
        )


def aggregate_report(costs: list[TaskCost]) -> dict[str, Any]:
    """Build the cost report: total spend and matched suite cost, never blended.

    See the module docstring for why the two views exist and why neither may be
    published as "the" cost.
    """

    comparison = comparison_attempts(costs)
    ordered = sorted(costs, key=lambda c: (c.task_id, c.mode, c.run_dir))
    excluded_variant_labeled_attempts = [
        {
            "task_id": tc.task_id,
            "mode": tc.mode,
            "variant_label": tc.variant_label,
            "run_dir": tc.run_dir,
            "cost_usd": tc.cost_usd,
        }
        for tc in ordered
        if tc.variant_label is not None
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
        "schema_version": SCHEMA_VERSION,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "selection_rule": SELECTION_RULE,
        "operational_economics": {
            "description": (
                "Total spend across every attempt, re-runs and unscored runs "
                "included. What was actually paid; not comparable across arms."
            ),
            "total_cost_usd": round(sum(tc.cost_usd for tc in costs), 6),
            "attempts": len(costs),
            "unpriced_models": unpriced_models,
            "cost_sources": _cost_source_summary(costs),
            **_buckets(costs, ATTEMPTS, ATTEMPTS, reconcile=True),
        },
        "comparison_economics": {
            "description": (
                "One selected attempt per (task_id, mode), restricted to tasks "
                "present in every arm. The only view an arm-to-arm cost claim "
                "may be built on."
            ),
            "total_cost_usd": round(sum(tc.cost_usd for tc in comparison.rows), 6),
            "tasks": len(comparison.task_ids),
            # list(), not the tuple itself: this dict is a JSON document, and its
            # array fields are read back as lists by every consumer and test.
            "modes": list(comparison.modes),
            "excluded_unmatched_task_ids": list(comparison.excluded_task_ids),
            "excluded_variant_labeled_attempts": excluded_variant_labeled_attempts,
            **_buckets(comparison.rows, TASKS_IN_ARM, TASKS_ACROSS_ARMS, reconcile=False),
            "per_cell": [_attempt_row(tc) for tc in comparison.rows],
        },
        "duplicate_attempts": _duplicate_attempts(
            [tc for tc in costs if tc.variant_label is None]
        ),
        "invalid_attempts": _invalid_attempts(ordered),
        "per_attempt": [_attempt_row(tc) for tc in ordered],
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
        help="Output path for cost_report.json "
        "(default: results/analysis/cost_report.json).",
    )
    parser.add_argument(
        "--benchmarks-root",
        type=Path,
        default=None,
        help="Benchmarks directory for task metadata (default: benchmarks/).",
    )
    parser.add_argument(
        "--legacy-score-contract",
        action="store_true",
        help=(
            "Read unversioned results under v1 score semantics. Mirrors the "
            "flag of the same name on analyze_scores.py — cost selection ranks "
            "attempts by the score that module produces, so the two have to be "
            "reading the corpus at the same contract or they collapse each "
            "cell to a different attempt."
        ),
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

    benchmarks_root = args.benchmarks_root or (PROJECT_ROOT / "benchmarks")
    output_path = args.output or (PROJECT_ROOT / "results" / "analysis" / "cost_report.json")
    result_dirs = args.results_dir or _discover_default_dirs(PROJECT_ROOT)

    if not result_dirs:
        logger.error("No result directories found.")
        return

    logger.info("Scanning %d result directories...", len(result_dirs))
    try:
        costs = scan_results_dirs(
            result_dirs,
            benchmarks_root,
            root=PROJECT_ROOT,
            allow_legacy=args.legacy_score_contract,
        )
    except ScoreContractError as exc:
        sys.exit(f"error: {exc}")
    logger.info("Found %d attempts with trace data.", len(costs))

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
