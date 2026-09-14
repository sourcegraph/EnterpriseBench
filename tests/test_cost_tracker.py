"""Tests for scripts/cost_tracker.py."""

from __future__ import annotations

import json
import math
import os
import sys
from pathlib import Path
from typing import Any

import pytest

# Make scripts importable
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))

from analyze_scores import load_all_results
from cost_tracker import (
    DEFAULT_MODEL,
    PRICING,
    SCHEMA_VERSION,
    ModelUsage,
    TaskCost,
    Usage,
    VendorUsage,
    aggregate_report,
    compute_cost,
    merge_model_usage,
    parse_model_usage,
    require_schema,
    scan_results_dirs,
    scan_trace,
    select_attempt,
    _parse_dir_identity,
)

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


def _write_trace(path: Path, entries: list[dict]) -> Path:
    """Write a list of dicts as JSONL to *path*."""
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w") as fh:
        for entry in entries:
            fh.write(json.dumps(entry) + "\n")
    return path


def _assistant_entry(
    input_tokens: int = 100,
    output_tokens: int = 50,
    cache_creation: int = 0,
    cache_read: int = 0,
    model: str = "claude-sonnet-4-6",
    request_id: str | None = None,
    message_id: str | None = None,
    block_type: str | None = None,
) -> dict:
    """Build an assistant trace entry — the one wire shape the parser reads.

    The request keys are optional so a single helper covers both trace
    generations: a legacy line that announces no request of its own, and a
    content-block line carrying ``requestId`` / ``message.id`` / ``content``.
    """
    message: dict = {
        "model": model,
        "role": "assistant",
        "usage": {
            "input_tokens": input_tokens,
            "output_tokens": output_tokens,
            "cache_creation_input_tokens": cache_creation,
            "cache_read_input_tokens": cache_read,
        },
    }
    if message_id is not None:
        message["id"] = message_id
    if block_type is not None:
        message["content"] = [{"type": block_type}]

    entry: dict = {"type": "assistant", "message": message}
    if request_id is not None:
        entry["requestId"] = request_id
    return entry


def _block_entry(
    request_id: str,
    output_tokens: int,
    block_type: str = "tool_use",
    input_tokens: int = 3,
    cache_creation: int = 2686,
    cache_read: int = 14244,
) -> dict:
    """An assistant entry shaped like a real EB content-block line.

    Real traces emit one line per assistant content block (thinking / text /
    tool_use). Every line of one API request repeats that request's keys and
    usage snapshot: input/cache are identical across the group, while
    ``output_tokens`` streams upward and is complete only on the final line.
    """
    return _assistant_entry(
        input_tokens=input_tokens,
        output_tokens=output_tokens,
        cache_creation=cache_creation,
        cache_read=cache_read,
        request_id=request_id,
        message_id=f"msg_{request_id}",
        block_type=block_type,
    )


def _synthetic_error_entry() -> dict:
    """A zero-usage ``<synthetic>`` isApiErrorMessage line — Claude Code's
    401/429 placeholder that must not latch the run's representative model."""
    return _assistant_entry(input_tokens=0, output_tokens=0, model="<synthetic>") | {
        "isApiErrorMessage": True
    }


# ---------------------------------------------------------------------------
# scan_trace
# ---------------------------------------------------------------------------


class TestParseTrace:
    def test_single_assistant_message(self, tmp_path: Path) -> None:
        trace = _write_trace(
            tmp_path / "agent_trace.jsonl",
            [_assistant_entry(input_tokens=200, output_tokens=80)],
        )
        usage = scan_trace(trace).usage
        assert usage.input_tokens == 200
        assert usage.output_tokens == 80
        assert usage.cache_write_tokens == 0
        assert usage.cache_read_tokens == 0
        assert usage.model == "claude-sonnet-4-6"
        assert usage.num_requests == 1

    def test_multiple_messages_summed(self, tmp_path: Path) -> None:
        trace = _write_trace(
            tmp_path / "agent_trace.jsonl",
            [
                _assistant_entry(input_tokens=100, output_tokens=50, cache_creation=10),
                {"type": "user", "message": {"role": "user"}},  # ignored
                _assistant_entry(input_tokens=200, output_tokens=30, cache_read=500),
            ],
        )
        usage = scan_trace(trace).usage
        assert usage.input_tokens == 300
        assert usage.output_tokens == 80
        assert usage.cache_write_tokens == 10
        assert usage.cache_read_tokens == 500
        assert usage.num_requests == 2

    def test_empty_trace(self, tmp_path: Path) -> None:
        trace = _write_trace(tmp_path / "agent_trace.jsonl", [])
        usage = scan_trace(trace).usage
        assert usage.input_tokens == 0
        assert usage.num_requests == 0
        assert usage.model == DEFAULT_MODEL

    def test_api_error_line_does_not_latch_model(self, tmp_path: Path) -> None:
        """A `<synthetic>` isApiErrorMessage line preceding a real, billed turn
        must not become the run's representative model — otherwise the real
        tokens get priced at DEFAULT_MODEL and the mispricing is hidden from
        unpriced_models (EnterpriseBench-qjfi)."""
        trace = _write_trace(
            tmp_path / "agent_trace.jsonl",
            [
                _synthetic_error_entry(),
                _assistant_entry(
                    input_tokens=1000, output_tokens=500, model="claude-opus-4-8"
                ),
            ],
        )
        usage = scan_trace(trace).usage
        assert usage.model == "claude-opus-4-8"
        assert usage.input_tokens == 1000
        assert usage.output_tokens == 500

    def test_pure_synthetic_trace_falls_back_to_default_model(
        self, tmp_path: Path
    ) -> None:
        """A run that only ever produced a `<synthetic>` error line names no real
        model, so it resolves to DEFAULT_MODEL at zero usage — priced (cost 0)
        rather than surfacing the sentinel as an unpriced model."""
        trace = _write_trace(
            tmp_path / "agent_trace.jsonl",
            [_synthetic_error_entry()],
        )
        usage = scan_trace(trace).usage
        assert usage.model == DEFAULT_MODEL
        assert usage.input_tokens == 0
        assert usage.output_tokens == 0

    def test_malformed_lines_skipped(self, tmp_path: Path) -> None:
        path = tmp_path / "agent_trace.jsonl"
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("w") as fh:
            fh.write("not valid json\n")
            fh.write(
                json.dumps(_assistant_entry(input_tokens=42, output_tokens=7)) + "\n"
            )
        usage = scan_trace(path).usage
        assert usage.input_tokens == 42
        assert usage.num_requests == 1

    def test_big_int_literal_line_skipped(self, tmp_path: Path) -> None:
        # A 4300+-digit integer literal makes json.loads raise a BARE ValueError
        # (CPython's int-str conversion guard, CVE-2020-10735), not the
        # JSONDecodeError subclass. It must be treated as a malformed line, not
        # abort the whole trace.
        path = tmp_path / "agent_trace.jsonl"
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("w") as fh:
            fh.write('{"input_tokens": ' + "9" * 5000 + "}\n")
            fh.write(
                json.dumps(_assistant_entry(input_tokens=42, output_tokens=7)) + "\n"
            )
        usage = scan_trace(path).usage
        assert usage.input_tokens == 42
        assert usage.num_requests == 1

    def test_deeply_nested_line_skipped(self, tmp_path: Path) -> None:
        # A ~10k-deep-nested line makes json.loads raise RecursionError (a
        # RuntimeError, NOT a ValueError subclass). It must be skipped too.
        path = tmp_path / "agent_trace.jsonl"
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("w") as fh:
            fh.write("[" * 10_000 + "]" * 10_000 + "\n")
            fh.write(
                json.dumps(_assistant_entry(input_tokens=42, output_tokens=7)) + "\n"
            )
        usage = scan_trace(path).usage
        assert usage.input_tokens == 42
        assert usage.num_requests == 1

    def test_non_assistant_entries_ignored(self, tmp_path: Path) -> None:
        trace = _write_trace(
            tmp_path / "agent_trace.jsonl",
            [
                {"type": "queue-operation", "operation": "enqueue"},
                {"type": "user", "message": {"role": "user"}},
            ],
        )
        usage = scan_trace(trace).usage
        assert usage.num_requests == 0

    def test_model_captured_from_first_assistant(self, tmp_path: Path) -> None:
        trace = _write_trace(
            tmp_path / "agent_trace.jsonl",
            [
                _assistant_entry(model="claude-opus-4-6"),
                _assistant_entry(model="claude-haiku-4-5"),
            ],
        )
        usage = scan_trace(trace).usage
        assert usage.model == "claude-opus-4-6"


# ---------------------------------------------------------------------------
# scan_trace — per-request dedup (EnterpriseBench-ewr8)
# ---------------------------------------------------------------------------


class TestRequestIdDedup:
    """A request's usage snapshot must be billed once, not once per content block.

    Fixtures mirror shapes taken from the real corpus.
    """

    def test_streaming_group_billed_once_at_final_output(self, tmp_path: Path) -> None:
        """Output streams upward across blocks (8 -> 335) on one request.

        Guards both failure directions: per-line summation triples input/cache
        and sums output to 343; first-per-request dedup would take output=8.
        """
        trace = _write_trace(
            tmp_path / "agent_trace.jsonl",
            [
                _block_entry("req_A", output_tokens=8, block_type="thinking"),
                _block_entry("req_A", output_tokens=335, block_type="tool_use"),
            ],
        )
        usage = scan_trace(trace).usage

        assert usage.output_tokens == 335  # final, not 8 (first) and not 343 (sum)
        assert usage.input_tokens == 3  # counted once, not 6
        assert usage.cache_write_tokens == 2686  # counted once, not 5372
        assert usage.cache_read_tokens == 14244  # counted once, not 28488
        assert usage.num_requests == 1  # one request, not two lines

    def test_replicated_final_group_billed_once(self, tmp_path: Path) -> None:
        """The writer may repeat the FINAL output on every block line.

        A real 7-line group each carrying output_tokens=937: per-line summation
        bills 6559 output tokens for 937.
        """
        trace = _write_trace(
            tmp_path / "agent_trace.jsonl",
            [_block_entry("req_B", output_tokens=937) for _ in range(7)],
        )
        usage = scan_trace(trace).usage

        assert usage.output_tokens == 937  # not 6559
        assert usage.input_tokens == 3  # not 21
        assert usage.num_requests == 1

    def test_group_lines_are_not_contiguous(self, tmp_path: Path) -> None:
        """Real groups are interleaved with user tool_result lines.

        Dedup must key on requestId, never on line adjacency.
        """
        trace = _write_trace(
            tmp_path / "agent_trace.jsonl",
            [
                _block_entry("req_C", output_tokens=10, block_type="thinking"),
                {"type": "user", "message": {"role": "user"}},
                _block_entry("req_C", output_tokens=500, block_type="tool_use"),
                {"type": "user", "message": {"role": "user"}},
                _block_entry("req_C", output_tokens=500, block_type="text"),
            ],
        )
        usage = scan_trace(trace).usage

        assert usage.output_tokens == 500
        assert usage.input_tokens == 3
        assert usage.num_requests == 1

    def test_distinct_requests_are_summed(self, tmp_path: Path) -> None:
        """Dedup is per request — separate requests still accumulate."""
        trace = _write_trace(
            tmp_path / "agent_trace.jsonl",
            [
                _block_entry("req_A", output_tokens=8, block_type="thinking"),
                _block_entry("req_A", output_tokens=100, block_type="tool_use"),
                _block_entry("req_B", output_tokens=250, cache_read=19655),
            ],
        )
        usage = scan_trace(trace).usage

        assert usage.output_tokens == 350  # 100 + 250
        assert usage.input_tokens == 6  # 3 + 3, one per request
        assert usage.cache_read_tokens == 14244 + 19655
        assert usage.num_requests == 2

    def test_zero_usage_error_record_does_not_erase_group(self, tmp_path: Path) -> None:
        """A trailing all-zero API-error record must not zero out a real group.

        This is why selection takes the max-output record rather than strictly
        the last one: an isApiErrorMessage record carries a truthy all-zero
        usage dict, and last-wins would bill the request at zero.
        """
        error_record = _block_entry(
            "req_D",
            output_tokens=0,
            input_tokens=0,
            cache_creation=0,
            cache_read=0,
        ) | {"isApiErrorMessage": True}
        trace = _write_trace(
            tmp_path / "agent_trace.jsonl",
            [_block_entry("req_D", output_tokens=812), error_record],
        )
        usage = scan_trace(trace).usage

        assert usage.output_tokens == 812
        assert usage.input_tokens == 3
        assert usage.num_requests == 1

    def test_message_id_groups_when_request_id_absent(self, tmp_path: Path) -> None:
        """message.id is the fallback grouping key if a writer drops requestId.

        Without it, a format change that dropped requestId would silently re-arm
        the per-content-block double-count with a green suite.
        """
        trace = _write_trace(
            tmp_path / "agent_trace.jsonl",
            [
                _assistant_entry(input_tokens=3, output_tokens=out, message_id="msg_E")
                for out in (8, 640)
            ],
        )
        usage = scan_trace(trace).usage

        assert usage.output_tokens == 640  # not 648
        assert usage.input_tokens == 3  # not 6
        assert usage.num_requests == 1

    def test_legacy_entries_without_any_key_still_counted_per_line(
        self, tmp_path: Path
    ) -> None:
        """Entries with neither requestId nor message.id keep per-line identity.

        They must not collapse into a single group — that would undercount
        turns on legacy/synthetic traces.
        """
        trace = _write_trace(
            tmp_path / "agent_trace.jsonl",
            [
                _assistant_entry(input_tokens=100, output_tokens=50),
                _assistant_entry(input_tokens=200, output_tokens=30),
            ],
        )
        usage = scan_trace(trace).usage

        assert usage.input_tokens == 300
        assert usage.output_tokens == 80
        assert usage.num_requests == 2


# ---------------------------------------------------------------------------
# compute_cost
# ---------------------------------------------------------------------------


class TestComputeCost:
    def test_sonnet_known_values(self) -> None:
        usage = Usage(
            input_tokens=1_000_000,
            output_tokens=1_000_000,
            cache_write_tokens=0,
            cache_read_tokens=0,
            model="claude-sonnet-4-6",
            num_requests=5,
        )
        cost = compute_cost(usage)
        # 1M * $3/M + 1M * $15/M = $18
        assert cost == 18.0

    def test_opus_known_values(self) -> None:
        usage = Usage(
            input_tokens=1_000_000,
            output_tokens=1_000_000,
            cache_write_tokens=0,
            cache_read_tokens=0,
            model="claude-opus-4-6",
            num_requests=3,
        )
        cost = compute_cost(usage)
        # 1M * $15/M + 1M * $75/M = $90
        assert cost == 90.0

    def test_haiku_with_cache(self) -> None:
        usage = Usage(
            input_tokens=500_000,
            output_tokens=100_000,
            cache_write_tokens=200_000,
            cache_read_tokens=300_000,
            model="claude-haiku-4-5",
            num_requests=2,
        )
        cost = compute_cost(usage)
        expected = (
            500_000 * 0.80 + 100_000 * 4.0 + 200_000 * 1.0 + 300_000 * 0.08
        ) / 1_000_000
        assert cost == round(expected, 6)

    @pytest.mark.parametrize(
        "model, expected",
        [
            # 1M input + 1M output at each model's own (input, output) rate.
            ("claude-fable-5", 60.0),  # $10/M + $50/M
            ("claude-opus-4-8", 30.0),  # $5/M + $25/M — not opus-4-6's $90
        ],
    )
    def test_newly_priced_model_known_values(
        self, model: str, expected: float
    ) -> None:
        usage = Usage(
            input_tokens=1_000_000,
            output_tokens=1_000_000,
            cache_write_tokens=0,
            cache_read_tokens=0,
            model=model,
            num_requests=4,
        )
        assert compute_cost(usage) == expected

    def test_opus_4_8_matches_vendor_costusd(self) -> None:
        """The real dep-traversal-010/condB opus-4-8 block prices to its own
        costUSD to the cent — the derivation these rates came from."""
        usage = Usage(
            input_tokens=296,
            output_tokens=13_680,
            cache_write_tokens=21_578,
            cache_read_tokens=579_879,
            model="claude-opus-4-8",
            num_requests=1,
        )
        assert compute_cost(usage) == 0.768282

    @pytest.mark.parametrize("model", sorted(PRICING))
    def test_pricing_follows_standard_multipliers(self, model: str) -> None:
        """Every PRICING row is derived from one input rate under Anthropic's
        standard multipliers (output 5x, cache_write 1.25x, cache_read 0.1x).
        A future price edit that touches one field without the others would
        silently break the derivation this table documents."""
        p = PRICING[model]
        assert p["output"] == pytest.approx(5.0 * p["input"])
        assert p["cache_write"] == pytest.approx(1.25 * p["input"])
        assert p["cache_read"] == pytest.approx(0.1 * p["input"])

    def test_unknown_model_falls_back_to_default(self) -> None:
        usage = Usage(
            input_tokens=1_000_000,
            output_tokens=0,
            cache_write_tokens=0,
            cache_read_tokens=0,
            model="claude-unknown-99",
            num_requests=1,
        )
        cost = compute_cost(usage)
        # Should fall back to sonnet pricing
        assert cost == 3.0

    def test_zero_tokens_zero_cost(self) -> None:
        usage = Usage(
            input_tokens=0,
            output_tokens=0,
            cache_write_tokens=0,
            cache_read_tokens=0,
            model="claude-sonnet-4-6",
            num_requests=0,
        )
        assert compute_cost(usage) == 0.0


# ---------------------------------------------------------------------------
# _parse_dir_identity
# ---------------------------------------------------------------------------


class TestParseDirIdentity:
    def test_baseline_run(self, tmp_path: Path) -> None:
        p = tmp_path / "runs" / "dep-traversal-001"
        task_id, mode, variant_label = _parse_dir_identity(p)
        assert task_id == "dep-traversal-001"
        assert mode == "baseline"
        assert variant_label is None

    def test_mcp_only(self, tmp_path: Path) -> None:
        p = tmp_path / "mcp_batch" / "dep-traversal-001_mcp_only"
        task_id, mode, variant_label = _parse_dir_identity(p)
        assert task_id == "dep-traversal-001"
        assert mode == "mcp_only"
        assert variant_label is None

    def test_hybrid(self, tmp_path: Path) -> None:
        p = tmp_path / "mcp_batch_v2" / "cal-drift-flask-config-001_hybrid"
        task_id, mode, variant_label = _parse_dir_identity(p)
        assert task_id == "cal-drift-flask-config-001"
        assert mode == "hybrid"
        assert variant_label is None

    def test_no_mode_suffix(self, tmp_path: Path) -> None:
        p = tmp_path / "mcp_batch" / "some-task-name"
        task_id, mode, variant_label = _parse_dir_identity(p)
        assert task_id == "some-task-name"
        assert mode == "unknown"
        assert variant_label is None


# ---------------------------------------------------------------------------
# scan_results_dirs
# ---------------------------------------------------------------------------


class TestScanResultsDirs:
    def test_scans_mcp_batch_dirs(self, tmp_path: Path) -> None:
        # Set up a fake mcp_batch directory
        task_dir = tmp_path / "mcp_batch" / "my-task_hybrid"
        _write_trace(
            task_dir / "agent_trace.jsonl",
            [_assistant_entry(input_tokens=1000, output_tokens=500)],
        )
        # Write task_metrics.json
        metrics = {"timing": {"agent": 42.5}}
        (task_dir / "task_metrics.json").write_text(json.dumps(metrics))

        # No benchmarks dir — will get "unknown" suite/difficulty
        costs = scan_results_dirs([tmp_path / "mcp_batch"], tmp_path / "benchmarks")
        assert len(costs) == 1
        assert costs[0].task_id == "my-task"
        assert costs[0].mode == "hybrid"
        assert costs[0].suite == "unknown"
        assert costs[0].agent_duration_seconds == 42.5
        assert costs[0].cost_usd > 0

    def test_missing_dir_skipped(self, tmp_path: Path) -> None:
        costs = scan_results_dirs([tmp_path / "nonexistent"], tmp_path / "benchmarks")
        assert costs == []

    def test_with_benchmarks_metadata(self, tmp_path: Path) -> None:
        # Set up benchmarks
        bench_dir = tmp_path / "benchmarks" / "customer_escalation" / "my-task"
        bench_dir.mkdir(parents=True)
        toml_content = b"""
[task]
id = "my-task"
suite = "customer_escalation"
difficulty = "hard"
"""
        (bench_dir / "task.toml").write_bytes(toml_content)

        # Set up trace
        task_dir = tmp_path / "mcp_batch" / "my-task_mcp_only"
        _write_trace(
            task_dir / "agent_trace.jsonl",
            [_assistant_entry(input_tokens=500, output_tokens=200)],
        )

        # Clear the cache from prior tests
        from cost_tracker import _TASK_META_CACHE

        _TASK_META_CACHE.clear()

        costs = scan_results_dirs([tmp_path / "mcp_batch"], tmp_path / "benchmarks")
        assert len(costs) == 1
        assert costs[0].suite == "customer_escalation"
        assert costs[0].difficulty == "hard"

        # Cleanup cache for other tests
        _TASK_META_CACHE.clear()


# ---------------------------------------------------------------------------
# aggregate_report
# ---------------------------------------------------------------------------


def _make_cost(
    task_id: str = "task-1",
    mode: str = "hybrid",
    suite: str = "customer_escalation",
    difficulty: str = "medium",
    input_tokens: int = 1000,
    output_tokens: int = 500,
    cost_usd: float = 0.01,
    model: str = "claude-sonnet-4-6",
    cost_source: str = "sdk",
    trace_cost_usd: float | None = None,
    run_dir: str | None = None,
    normalized_score: float | None = 1.0,
    trace_timestamp: str = "",
) -> TaskCost:
    """Build a TaskCost record for aggregate_report tests.

    ``cost_source`` selects the tier by building a vendor block or omitting one —
    the record derives cost, source and model list from that single fact, so a
    record that claims one tier while carrying the other's numbers cannot be
    built here either.

    ``run_dir`` defaults to a value unique per (task_id, mode), so a test that
    does not care about re-runs gets one attempt per cell rather than an
    accidental duplicate. ``normalized_score`` defaults to a scored (valid)
    attempt; pass None for one the scoring layer never produced a score for.
    """
    vendor = (
        VendorUsage(
            models=(ModelUsage(model, input_tokens, output_tokens, 0, 0, cost_usd),),
            total_cost_usd=cost_usd,
        )
        if cost_source == "sdk"
        else None
    )
    return TaskCost(
        task_id=task_id,
        mode=mode,
        suite=suite,
        difficulty=difficulty,
        usage=Usage(
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            cache_write_tokens=0,
            cache_read_tokens=0,
            model=model,
            num_requests=3,
        ),
        trace_cost_usd=cost_usd if trace_cost_usd is None else trace_cost_usd,
        vendor=vendor,
        agent_duration_seconds=60.0,
        run_dir=run_dir if run_dir is not None else f"results/{task_id}_{mode}",
        normalized_score=normalized_score,
        trace_timestamp=trace_timestamp,
    )


class TestAggregateReport:
    def test_report_structure(self) -> None:
        costs = [_make_cost()]
        report = aggregate_report(costs)
        assert "generated_at" in report
        op = report["operational_economics"]
        assert op["attempts"] == 1
        assert op["total_cost_usd"] == 0.01
        assert {"by_mode", "by_suite", "by_difficulty"} <= op.keys()
        assert len(report["per_attempt"]) == 1

    def test_mode_breakdown(self) -> None:
        costs = [
            _make_cost(task_id="t1", mode="hybrid", cost_usd=1.0),
            _make_cost(task_id="t2", mode="hybrid", cost_usd=2.0),
            _make_cost(task_id="t3", mode="mcp_only", cost_usd=0.5),
        ]
        by_mode = aggregate_report(costs)["operational_economics"]["by_mode"]
        assert by_mode["hybrid"]["attempts"] == 2
        assert by_mode["hybrid"]["total_cost_usd"] == 3.0
        assert by_mode["hybrid"]["avg_cost_per_attempt"] == 1.5
        assert by_mode["mcp_only"]["attempts"] == 1

    def test_suite_breakdown(self) -> None:
        costs = [
            _make_cost(task_id="t1", suite="incident_response"),
            _make_cost(task_id="t2", suite="incident_response"),
            _make_cost(task_id="t3", suite="feature_delivery"),
        ]
        by_suite = aggregate_report(costs)["operational_economics"]["by_suite"]
        assert by_suite["incident_response"]["attempts"] == 2
        assert by_suite["feature_delivery"]["attempts"] == 1

    def test_difficulty_breakdown(self) -> None:
        costs = [
            _make_cost(task_id="t1", difficulty="easy"),
            _make_cost(task_id="t2", difficulty="hard"),
        ]
        by_difficulty = aggregate_report(costs)["operational_economics"][
            "by_difficulty"
        ]
        assert {"easy", "hard"} <= by_difficulty.keys()

    def test_empty_costs(self) -> None:
        report = aggregate_report([])
        assert report["operational_economics"]["attempts"] == 0
        assert report["operational_economics"]["total_cost_usd"] == 0.0
        assert report["comparison_economics"]["tasks"] == 0
        assert report["comparison_economics"]["total_cost_usd"] == 0.0
        assert report["per_attempt"] == []

    def test_per_attempt_sorted_by_id(self) -> None:
        costs = [
            _make_cost(task_id="z-task"),
            _make_cost(task_id="a-task"),
        ]
        report = aggregate_report(costs)
        assert report["per_attempt"][0]["task_id"] == "a-task"
        assert report["per_attempt"][1]["task_id"] == "z-task"

    def test_per_attempt_fields(self) -> None:
        costs = [_make_cost(input_tokens=999, output_tokens=111)]
        entry = aggregate_report(costs)["per_attempt"][0]
        assert entry["input_tokens"] == 999
        assert entry["output_tokens"] == 111
        assert entry["model"] == "claude-sonnet-4-6"
        assert "cache_write_tokens" in entry
        assert "cache_read_tokens" in entry
        assert "agent_duration_seconds" in entry


class TestUnpricedModelDisclosure:
    """An unpriced model must be disclosed whenever PRICING actually billed it.

    Only the trace-derived population is exposed to PRICING. There, an unknown
    model is billed at DEFAULT_MODEL rates, and such models cluster in a single
    arm, so the substitution corrupts the arm-to-arm delta this report exists to
    support — the caveat has to ride in the JSON.

    A vendor-priced run is a different story: Claude Code reports costUSD for
    every model it saw, so an unpriced model there is priced correctly and must
    NOT be flagged. Flagging it would be a false alarm attached to the arm that
    happens to use the newest models (EnterpriseBench-qjfi).
    """

    def test_all_priced_reports_no_unpriced_models(self) -> None:
        report = aggregate_report([_make_cost(task_id="t1", cost_source="trace")])
        assert report["operational_economics"]["unpriced_models"] == []

    def test_unpriced_model_is_surfaced_when_trace_derived(self) -> None:
        report = aggregate_report(
            [
                _make_cost(
                    task_id="t1", model="claude-sonnet-4-6", cost_source="trace"
                ),
                _make_cost(
                    task_id="t2", model="claude-unknown-99", cost_source="trace"
                ),
            ]
        )
        assert report["operational_economics"]["unpriced_models"] == ["claude-unknown-99"]

    def test_unpriced_models_deduped_and_sorted(self) -> None:
        report = aggregate_report(
            [
                _make_cost(
                    task_id="t1", model="claude-unknown-99", cost_source="trace"
                ),
                _make_cost(
                    task_id="t2", model="claude-unknown-42", cost_source="trace"
                ),
                _make_cost(
                    task_id="t3", model="claude-unknown-99", cost_source="trace"
                ),
            ]
        )
        assert report["operational_economics"]["unpriced_models"] == ["claude-unknown-42", "claude-unknown-99"]

    def test_vendor_priced_model_is_not_flagged_unpriced(self) -> None:
        """A vendor-priced run is billed from costUSD — PRICING never touches it,
        so even a model absent from PRICING must not be flagged."""
        report = aggregate_report(
            [
                _make_cost(task_id="t1", model="claude-unknown-99", cost_source="sdk"),
                _make_cost(task_id="t2", model="claude-unknown-42", cost_source="sdk"),
            ]
        )
        assert report["operational_economics"]["unpriced_models"] == []

    def test_now_priced_models_not_flagged_when_trace_derived(self) -> None:
        """fable-5 and opus-4-8 are in PRICING now, so a tier-2 run of either is
        billed at its real rate and must not be flagged as unpriced."""
        report = aggregate_report(
            [
                _make_cost(task_id="t1", model="claude-fable-5", cost_source="trace"),
                _make_cost(task_id="t2", model="claude-opus-4-8", cost_source="trace"),
            ]
        )
        assert report["operational_economics"]["unpriced_models"] == []


# ---------------------------------------------------------------------------
# Vendor modelUsage (tier 1)
# ---------------------------------------------------------------------------


def _write_stdout(path: Path, payload: object) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload) if not isinstance(payload, str) else payload)
    return path


def _model_entry(
    input_tokens: int = 100,
    output_tokens: int = 50,
    cache_write: int = 10,
    cache_read: int = 5,
    cost: float = 0.5,
) -> dict:
    return {
        "inputTokens": input_tokens,
        "outputTokens": output_tokens,
        "cacheCreationInputTokens": cache_write,
        "cacheReadInputTokens": cache_read,
        "costUSD": cost,
    }


# Sentinels for _vendor: derive the total from the block, or omit it entirely.
_AUTO = object()
_OMIT = object()


def _vendor(block: dict, total: object = _AUTO) -> dict:
    """A vendor result object: a modelUsage block and the total that rides with it.

    The real writer never emits one without the other, so tests get a reconciling
    total by default; pass ``total`` to break the checksum on purpose, or ``_OMIT``
    to drop it.
    """
    payload: dict = {"modelUsage": block}
    if total is _AUTO:
        entries = (
            list(block.values())
            if any(isinstance(v, dict) for v in block.values())
            else [block]
        )
        total = sum(e.get("costUSD", 0) for e in entries if isinstance(e, dict))
    if total is not _OMIT:
        payload["total_cost_usd"] = total
    return payload


class TestParseModelUsage:
    def test_per_model_block(self, tmp_path: Path) -> None:
        p = _write_stdout(
            tmp_path / "agent_stdout.log",
            _vendor({"claude-sonnet-4-6": _model_entry(cost=1.25)}),
        )
        vendor = parse_model_usage(p)
        (usage,) = vendor.models
        assert usage.model == "claude-sonnet-4-6"
        assert usage.input_tokens == 100
        assert usage.output_tokens == 50
        assert usage.cache_write_tokens == 10
        assert usage.cache_read_tokens == 5
        assert usage.cost_usd == 1.25
        assert vendor.total_cost_usd == 1.25

    def test_multi_model_block_returns_every_model(self, tmp_path: Path) -> None:
        """The haiku sub-agent spend the trace never sees (EnterpriseBench-jepu)."""
        p = _write_stdout(
            tmp_path / "agent_stdout.log",
            _vendor(
                {
                    "claude-sonnet-4-6": _model_entry(output_tokens=900, cost=2.0),
                    "claude-haiku-4-5": _model_entry(output_tokens=100, cost=0.1),
                }
            ),
        )
        usages = parse_model_usage(p).models
        assert [u.model for u in usages] == ["claude-haiku-4-5", "claude-sonnet-4-6"]
        assert sum(u.cost_usd for u in usages) == 2.1

    def test_stream_json_takes_last_block(self, tmp_path: Path) -> None:
        """Earlier stream lines carry partial totals; the result message is last."""
        lines = [
            json.dumps({"type": "assistant"}),
            json.dumps(_vendor({"claude-sonnet-4-6": _model_entry(cost=0.5)})),
            json.dumps(_vendor({"claude-sonnet-4-6": _model_entry(cost=3.0)})),
        ]
        p = _write_stdout(tmp_path / "agent_stdout.log", "\n".join(lines))
        (usage,) = parse_model_usage(p).models
        assert usage.cost_usd == 3.0

    def test_missing_file(self, tmp_path: Path) -> None:
        assert parse_model_usage(tmp_path / "nope.log") is None


class TestMergeModelUsage:
    def test_tokens_summed_across_models(self) -> None:
        usage = merge_model_usage(
            (
                ModelUsage("claude-sonnet-4-6", 100, 900, 10, 5, 2.0),
                ModelUsage("claude-haiku-4-5", 20, 100, 1, 2, 0.1),
            ),
            num_requests=7,
        )
        assert usage.input_tokens == 120
        assert usage.output_tokens == 1000
        assert usage.cache_write_tokens == 11
        assert usage.cache_read_tokens == 7
        assert usage.num_requests == 7

    def test_primary_model_is_the_largest_spender(self) -> None:
        """Not the first-seen model: a haiku title call must not label the run."""
        usage = merge_model_usage(
            (
                ModelUsage("claude-haiku-4-5", 20, 100, 0, 0, 0.1),
                ModelUsage("claude-sonnet-4-6", 100, 900, 0, 0, 2.0),
            ),
            num_requests=3,
        )
        assert usage.model == "claude-sonnet-4-6"


# ---------------------------------------------------------------------------
# Tier resolution: vendor cost preferred, trace derivation as fallback
# ---------------------------------------------------------------------------


class TestCostSourceResolution:
    """Cost must come from the vendor whenever the vendor reported one.

    The trace derivation cannot price a multi-model run (it sees one model and no
    sub-agent usage), and it undercounts truncated traces. Where both exist the
    vendor wins; the derived figure is kept only for reconciliation.
    """

    def _run_dir(self, tmp_path: Path, name: str = "my-task_hybrid") -> Path:
        task_dir = tmp_path / "mcp_batch" / name
        _write_trace(
            task_dir / "agent_trace.jsonl",
            [_assistant_entry(input_tokens=1000, output_tokens=500)],
        )
        return task_dir

    def test_vendor_cost_preferred_over_trace(self, tmp_path: Path) -> None:
        task_dir = self._run_dir(tmp_path)
        _write_stdout(
            task_dir / "agent_stdout.log",
            _vendor(
                {
                    "claude-sonnet-4-6": _model_entry(
                        input_tokens=4000, output_tokens=2000, cost=9.99
                    )
                }
            ),
        )
        (cost,) = scan_results_dirs([tmp_path / "mcp_batch"], tmp_path / "benchmarks")

        assert cost.cost_source == "sdk"
        assert cost.cost_usd == 9.99
        # Tokens are the vendor's, not the trace's 1000/500.
        assert cost.usage.input_tokens == 4000
        assert cost.usage.output_tokens == 2000
        # The derived figure survives for reconciliation and disagrees.
        assert cost.trace_cost_usd != cost.cost_usd
        assert cost.trace_cost_usd > 0

    def test_multi_model_run_bills_every_model(self, tmp_path: Path) -> None:
        """Sub-agent spend is invisible to the trace but present in the vendor block."""
        task_dir = self._run_dir(tmp_path)
        _write_stdout(
            task_dir / "agent_stdout.log",
            _vendor(
                {
                    "claude-sonnet-4-6": _model_entry(cost=2.0),
                    "claude-haiku-4-5": _model_entry(cost=0.5),
                }
            ),
        )
        (cost,) = scan_results_dirs([tmp_path / "mcp_batch"], tmp_path / "benchmarks")

        assert cost.cost_usd == 2.5
        assert set(cost.models) == {"claude-sonnet-4-6", "claude-haiku-4-5"}

    def test_falls_back_to_trace_when_no_vendor_block(self, tmp_path: Path) -> None:
        task_dir = self._run_dir(tmp_path)
        _write_stdout(task_dir / "agent_stdout.log", {"total_cost_usd": 1.0})
        (cost,) = scan_results_dirs([tmp_path / "mcp_batch"], tmp_path / "benchmarks")

        assert cost.cost_source == "trace"
        assert cost.cost_usd > 0
        # On a fallback run the two figures are the same number by construction.
        assert cost.cost_usd == cost.trace_cost_usd
        assert cost.usage.input_tokens == 1000

    def test_falls_back_when_stdout_absent(self, tmp_path: Path) -> None:
        self._run_dir(tmp_path)
        (cost,) = scan_results_dirs([tmp_path / "mcp_batch"], tmp_path / "benchmarks")
        assert cost.cost_source == "trace"

    def test_num_requests_stays_trace_derived(self, tmp_path: Path) -> None:
        """The vendor block reports no request count; the trace is the only source."""
        task_dir = self._run_dir(tmp_path)
        _write_stdout(
            task_dir / "agent_stdout.log",
            _vendor({"claude-sonnet-4-6": _model_entry()}),
        )
        (cost,) = scan_results_dirs([tmp_path / "mcp_batch"], tmp_path / "benchmarks")
        assert cost.usage.num_requests == 1


class TestReconciliationDisclosure:
    """Where cost came from, and how far the trace missed, must be in the JSON.

    A reader of cost_report.json cannot see a logger warning. If some runs are
    still trace-derived they carry the old distortion, and mixing them into a
    total silently is the failure this block exists to prevent.
    """

    def test_cost_source_counts(self) -> None:
        report = aggregate_report(
            [
                _make_cost(task_id="t1", cost_source="sdk"),
                _make_cost(task_id="t2", cost_source="sdk"),
                _make_cost(task_id="t3", cost_source="trace"),
            ]
        )
        assert report["operational_economics"]["cost_sources"]["sdk"] == 2
        assert report["operational_economics"]["cost_sources"]["trace"] == 1

    def test_trace_derived_attempts_are_named_by_run_dir(self) -> None:
        """The key is the attempt, not the cell.

        One attempt of a re-run cell can be trace-derived while another is
        vendor-priced; a cell-level key would pin the caveat on both.
        """
        report = aggregate_report(
            [
                _make_cost(task_id="good", cost_source="sdk"),
                _make_cost(
                    task_id="degraded",
                    mode="baseline",
                    cost_source="trace",
                    run_dir="results/mcp_batch_v3/degraded_baseline",
                ),
            ]
        )
        assert report["operational_economics"]["cost_sources"][
            "trace_derived_attempts"
        ] == ["degraded:baseline@results/mcp_batch_v3/degraded_baseline"]

    def test_one_attempt_of_a_rerun_cell_can_be_named_alone(self) -> None:
        report = aggregate_report(
            [
                _make_cost(task_id="t1", run_dir="a", cost_source="sdk"),
                _make_cost(task_id="t1", run_dir="b", cost_source="trace"),
            ]
        )
        named = report["operational_economics"]["cost_sources"][
            "trace_derived_attempts"
        ]
        assert named == ["t1:hybrid@b"]

    def test_reconciliation_delta_is_published(self) -> None:
        # Vendor says $10; the old trace derivation would have said $4.
        report = aggregate_report(
            [_make_cost(task_id="t1", cost_usd=10.0, trace_cost_usd=4.0)]
        )
        rec = report["operational_economics"]["cost_sources"]["reconciliation"]
        assert rec["vendor_cost_usd"] == 10.0
        assert rec["trace_derived_cost_usd"] == 4.0
        assert rec["delta_usd"] == 6.0
        assert rec["trace_over_vendor_ratio"] == 0.4

    def test_reconciliation_ignores_fallback_runs(self) -> None:
        """A trace-sourced run agrees with itself; averaging it in would hide the gap."""
        report = aggregate_report(
            [
                _make_cost(task_id="t1", cost_usd=10.0, trace_cost_usd=4.0),
                _make_cost(task_id="t2", cost_usd=5.0, cost_source="trace"),
            ]
        )
        rec = report["operational_economics"]["cost_sources"]["reconciliation"]
        assert rec["vendor_cost_usd"] == 10.0
        assert rec["trace_derived_cost_usd"] == 4.0

    def test_empty_costs_reconcile_without_dividing_by_zero(self) -> None:
        rec = aggregate_report([])["operational_economics"]["cost_sources"]["reconciliation"]
        assert rec["trace_over_vendor_ratio"] == 0.0

    def test_reconciliation_is_published_per_arm(self) -> None:
        """The distortion is per-arm, so a blended ratio alone would conceal it.

        Both arms below bill $10 of vendor cost, so the overall ratio sits at 0.5
        and looks uniform. It is not: the trace under-derives baseline by 4x and
        mcp_only not at all — exactly the skew that corrupts an arm-to-arm delta
        while the global number says nothing is wrong.
        """
        report = aggregate_report(
            [
                _make_cost(
                    task_id="t1", mode="baseline", cost_usd=10.0, trace_cost_usd=2.5
                ),
                _make_cost(
                    task_id="t2", mode="mcp_only", cost_usd=10.0, trace_cost_usd=7.5
                ),
            ]
        )
        assert (
            report["operational_economics"]["cost_sources"]["reconciliation"]["trace_over_vendor_ratio"] == 0.5
        )
        by_mode = report["operational_economics"]["by_mode"]
        assert by_mode["baseline"]["reconciliation"]["trace_over_vendor_ratio"] == 0.25
        assert by_mode["mcp_only"]["reconciliation"]["trace_over_vendor_ratio"] == 0.75

    def test_bucket_reconciliation_ignores_fallback_runs(self) -> None:
        """A tier-2 bucket has nothing to reconcile: the two figures are one number."""
        report = aggregate_report(
            [_make_cost(task_id="t1", mode="baseline", cost_source="trace")]
        )
        rec = report["operational_economics"]["by_mode"]["baseline"]["reconciliation"]
        assert rec["vendor_cost_usd"] == 0.0
        assert rec["trace_over_vendor_ratio"] == 0.0


class TestConsumerContract:
    """generate_report.py and generate_charts.py read these keys. Do not drop them.

    Both views are pinned, not just the one a given consumer happens to render:
    dropping either is what re-creates the single ambiguous total.
    """

    def test_keys_generate_report_reads(self) -> None:
        report = aggregate_report([_make_cost(mode="hybrid", cost_usd=2.0)])
        assert report["schema_version"] == SCHEMA_VERSION

        op = report["operational_economics"]
        assert {"total_cost_usd", "attempts", "by_mode"} <= op.keys()
        assert {"attempts", "total_cost_usd", "avg_cost_per_attempt"} <= op["by_mode"][
            "hybrid"
        ].keys()

        comp = report["comparison_economics"]
        assert {"total_cost_usd", "tasks", "modes", "by_mode"} <= comp.keys()
        assert {"tasks", "total_cost_usd", "avg_cost_per_task"} <= comp["by_mode"][
            "hybrid"
        ].keys()

    def test_keys_generate_charts_reads(self) -> None:
        report = aggregate_report([_make_cost(task_id="t1", mode="hybrid")])
        entry = report["comparison_economics"]["per_cell"][0]
        assert {"task_id", "mode", "cost_usd"} <= entry.keys()
        assert (
            "total_cost_usd" in report["comparison_economics"]["by_mode"]["hybrid"]
        )


# Every block parse_model_usage must refuse to bill. The id names the reason, so a
# failure report says which shape regressed.
_UNBILLABLE_PAYLOADS = [
    pytest.param("", id="empty_file"),
    pytest.param({"total_cost_usd": 1.0}, id="no_modelUsage_block"),
    pytest.param("not json at all {{{", id="malformed_content"),
    # Entries that cannot be read whole. Summing only the ones that parsed would
    # bill the run short, with nothing in the output saying so.
    pytest.param(_vendor({"junk": 1}), id="no_usable_model_entries"),
    pytest.param(
        _vendor(
            {
                "claude-sonnet-4-6": _model_entry(cost=2.0),
                "claude-haiku-4-5": {"inputTokens": 1000},  # no costUSD
            },
            total=3.0,
        ),
        id="model_entry_missing_costUSD_would_bill_2_of_3",
    ),
    pytest.param(
        _vendor(
            {
                "claude-sonnet-4-6": _model_entry(cost=2.0),
                "claude-haiku-4-5": "truncated",
            },
            total=2.0,
        ),
        id="non_dict_model_entry",
    ),
    # The checksum itself: the parts must agree with the vendor's own total, and a
    # block with no usable total cannot be checked at all, so it is rejected rather
    # than accepted unverified.
    pytest.param(
        _vendor({"claude-sonnet-4-6": _model_entry(cost=2.0)}, total=3.0),
        id="parts_do_not_sum_to_vendor_total",
    ),
    pytest.param(
        _vendor({"claude-sonnet-4-6": _model_entry(cost=2.0)}, total=_OMIT),
        id="no_total_means_no_checksum",
    ),
    pytest.param(
        _vendor({"claude-sonnet-4-6": _model_entry(cost=2.0)}, total="2.0"),
        id="non_numeric_total",
    ),
    # Numbers json.loads accepts but no one can bill. NaN is the dangerous one:
    # every comparison against it is False, so it passes the checksum untouched and
    # then turns the whole report's total into NaN — and cost_report.json into
    # invalid JSON.
    pytest.param(
        '{"modelUsage": {"claude-sonnet-4-6": {"costUSD": NaN}}, '
        '"total_cost_usd": 2.0}',
        id="nan_cost",
    ),
    pytest.param(
        '{"modelUsage": {"claude-sonnet-4-6": {"costUSD": 2.0}}, '
        '"total_cost_usd": NaN}',
        id="nan_total",
    ),
    pytest.param(
        _vendor({"claude-sonnet-4-6": {"costUSD": True}}, total=1.0),
        id="bool_cost_would_bill_1_usd",  # bool subclasses int
    ),
    pytest.param(
        _vendor({"claude-sonnet-4-6": {"costUSD": None}}, total=2.0),
        id="null_cost_would_fold_to_zero",
    ),
    # int(float("inf")) raises OverflowError, not ValueError — uncaught it would
    # escape the per-run fallback and abort the whole batch scan.
    pytest.param(
        '{"modelUsage": {"claude-sonnet-4-6": '
        '{"inputTokens": Infinity, "costUSD": 2.0}}, "total_cost_usd": 2.0}',
        id="infinite_token_count",
    ),
    # Last-wins picks the trailing partial stream block; the checksum catches it.
    pytest.param(
        "\n".join(
            [
                json.dumps(
                    _vendor({"claude-sonnet-4-6": _model_entry(cost=3.0)}, total=3.0)
                ),
                json.dumps(
                    _vendor({"claude-sonnet-4-6": _model_entry(cost=0.5)}, total=3.0)
                ),
            ]
        ),
        id="trailing_partial_stream_block",
    ),
]


class TestVendorBlockIntegrity:
    """A partially-readable vendor block must be rejected, never billed short.

    This is the one input that can turn the authoritative source into a plausible
    *undercount*: drop one unreadable model and the run still bills, just for
    less, with nothing in the output saying so. A trace fallback is worse but it
    is disclosed (cost_sources.trace_derived_task_ids); a silent short bill is
    not. Every case here must return None so the caller falls back loudly.

    The vendor writes its own total_cost_usd beside the block, which makes the
    per-model records checkable against a checksum rather than merely parseable.
    """

    @pytest.mark.parametrize("payload", _UNBILLABLE_PAYLOADS)
    def test_unbillable_block_is_rejected(
        self, tmp_path: Path, payload: object
    ) -> None:
        """Every unreadable shape falls back loudly. None of them bills short."""
        p = _write_stdout(tmp_path / "agent_stdout.log", payload)
        assert parse_model_usage(p) is None

    def test_block_passing_the_checksum_is_accepted(self, tmp_path: Path) -> None:
        p = _write_stdout(
            tmp_path / "agent_stdout.log",
            _vendor(
                {
                    "claude-sonnet-4-6": _model_entry(cost=2.0),
                    "claude-haiku-4-5": _model_entry(cost=0.5),
                },
                total=2.5,
            ),
        )
        assert parse_model_usage(p).total_cost_usd == 2.5

    def test_billed_cost_is_the_vendor_total_not_a_resummation(
        self, tmp_path: Path
    ) -> None:
        """The vendor computed the bottom line; re-deriving it is this module's bug.

        Within checksum tolerance the parts can differ from the vendor's own total.
        The total is what gets billed.
        """
        p = _write_stdout(
            tmp_path / "agent_stdout.log",
            _vendor({"claude-sonnet-4-6": _model_entry(cost=2.0)}, total=2.008),
        )
        vendor = parse_model_usage(p)
        assert vendor.total_cost_usd == 2.008
        assert sum(m.cost_usd for m in vendor.models) == 2.0

    def test_checksum_tolerates_float_summation_drift(self, tmp_path: Path) -> None:
        """0.1 + 0.2 != 0.3 in binary floating point; that must not reject a run."""
        p = _write_stdout(
            tmp_path / "agent_stdout.log",
            _vendor(
                {
                    "claude-sonnet-4-6": _model_entry(cost=0.1),
                    "claude-haiku-4-5": _model_entry(cost=0.2),
                },
                total=0.3,
            ),
        )
        assert len(parse_model_usage(p).models) == 2

    def test_rejected_block_falls_back_to_trace_not_to_zero(
        self, tmp_path: Path
    ) -> None:
        """End to end: a corrupt block must produce a disclosed trace-derived run."""
        task_dir = tmp_path / "mcp_batch" / "my-task_hybrid"
        _write_trace(
            task_dir / "agent_trace.jsonl",
            [_assistant_entry(input_tokens=1000, output_tokens=500)],
        )
        _write_stdout(
            task_dir / "agent_stdout.log",
            _vendor({"claude-sonnet-4-6": _model_entry(cost=1.0)}, total=9.0),
        )
        (cost,) = scan_results_dirs([tmp_path / "mcp_batch"], tmp_path / "benchmarks")

        assert cost.cost_source == "trace"
        assert cost.cost_usd > 0  # not billed at the partial $1.00, and not $0
        assert cost.cost_usd == cost.trace_cost_usd

        report = aggregate_report([cost])
        sources = report["operational_economics"]["cost_sources"]
        assert sources["trace"] == 1
        assert sources["trace_derived_attempts"] == [f"my-task:hybrid@{cost.run_dir}"]

    def test_nan_run_cannot_poison_the_batch_total(self, tmp_path: Path) -> None:
        """One malformed run must not take the whole report down with it.

        A NaN cost does not stay in its own row: it propagates through the batch
        sum, so every aggregate in the report becomes NaN, and json.dump writes the
        bare token NaN — which is not valid JSON and breaks strict consumers. The
        blast radius is the reason the NaN is rejected at the boundary.
        """
        for name, payload in (
            (
                "good-task_hybrid",
                _vendor({"claude-sonnet-4-6": _model_entry(cost=2.0)}),
            ),
            (
                "bad-task_hybrid",
                '{"modelUsage": {"claude-sonnet-4-6": {"costUSD": NaN}}, '
                '"total_cost_usd": NaN}',
            ),
        ):
            task_dir = tmp_path / "mcp_batch" / name
            _write_trace(
                task_dir / "agent_trace.jsonl",
                [_assistant_entry(input_tokens=1000, output_tokens=500)],
            )
            _write_stdout(task_dir / "agent_stdout.log", payload)

        costs = scan_results_dirs([tmp_path / "mcp_batch"], tmp_path / "benchmarks")
        report = aggregate_report(costs)

        assert math.isfinite(report["operational_economics"]["total_cost_usd"])
        assert report["operational_economics"]["total_cost_usd"] > 0
        # The corrupt run is disclosed as trace-derived, not billed at NaN.
        (named,) = report["operational_economics"]["cost_sources"][
            "trace_derived_attempts"
        ]
        assert named.startswith("bad-task:hybrid@")
        # And the report is still strictly valid JSON (allow_nan would emit `NaN`).
        json.loads(json.dumps(report, allow_nan=False))


# ---------------------------------------------------------------------------
# The two economics views (EnterpriseBench-jrgs)
# ---------------------------------------------------------------------------


def _write_attempt(
    root: Path,
    batch: str,
    task_id: str,
    mode: str,
    *,
    score: float | None = 1.0,
    timestamp: str | None = None,
    input_tokens: int = 1000,
    output_tokens: int = 500,
) -> Path:
    """Write one attempt directory: agent_trace.jsonl plus results.json.

    ``score=None`` writes no results.json at all — an attempt the scoring layer
    never produced a score for, which is what makes it invalid rather than free.
    """
    task_dir = root / batch / f"{task_id}_{mode}"
    entry = _assistant_entry(input_tokens=input_tokens, output_tokens=output_tokens)
    if timestamp is not None:
        entry = entry | {"timestamp": timestamp}
    _write_trace(task_dir / "agent_trace.jsonl", [entry])

    if score is not None:
        (task_dir / "results.json").write_text(
            json.dumps(
                {
                    "task_id": task_id,
                    "scores": {
                        "checkpoints_total": 1,
                        "task_score": score,
                        # Cost selection reads the score through
                        # analyze_scores.parse_result, which refuses a result
                        # that does not say which contract its task_score is
                        # written at (eb_verify.score_contract).
                        "score_contract_version": 2,
                        "checkpoints": [],
                    },
                    "task_metadata": {"suite": "customer_escalation",
                                      "difficulty": "medium"},
                    "config": {"mode": mode},
                }
            )
        )
    return task_dir


class TestAttemptIdentity:
    """Two runs of the same (task_id, mode) are two attempts, not one row.

    Before this, ``scan_results_dirs`` emitted anonymous duplicate rows: on the
    live corpus 441 rows carried only 426 distinct pairs, and nothing in the
    report said so.
    """

    def test_each_attempt_carries_its_own_run_dir(self, tmp_path: Path) -> None:
        _write_attempt(tmp_path, "mcp_batch", "t1", "hybrid")
        _write_attempt(tmp_path, "mcp_batch_v2", "t1", "hybrid")

        costs = scan_results_dirs(
            [tmp_path / "mcp_batch", tmp_path / "mcp_batch_v2"],
            tmp_path / "benchmarks",
        )
        assert len(costs) == 2
        assert len({c.run_dir for c in costs}) == 2
        assert {(c.task_id, c.mode) for c in costs} == {("t1", "hybrid")}

    def test_run_dir_is_relative_to_the_project_root(self, tmp_path: Path) -> None:
        """cost_report.json is tracked, so an absolute path would commit a home
        directory and make run_dir — a tiebreak key — differ per checkout."""
        _write_attempt(tmp_path, "mcp_batch", "t1", "hybrid")
        (cost,) = scan_results_dirs(
            [tmp_path / "mcp_batch"], tmp_path / "benchmarks", root=tmp_path
        )
        assert cost.run_dir == "mcp_batch/t1_hybrid"

    def test_run_dir_outside_the_root_stays_absolute(self, tmp_path: Path) -> None:
        outside = tmp_path / "elsewhere"
        _write_attempt(outside, "mcp_batch", "t1", "hybrid")
        (cost,) = scan_results_dirs(
            [outside / "mcp_batch"], tmp_path / "benchmarks", root=tmp_path / "root"
        )
        assert Path(cost.run_dir).is_absolute()

    def test_trace_timestamp_is_read_from_the_trace(self, tmp_path: Path) -> None:
        _write_attempt(
            tmp_path, "mcp_batch", "t1", "hybrid", timestamp="2026-03-31T22:14:36.052Z"
        )
        (cost,) = scan_results_dirs([tmp_path / "mcp_batch"], tmp_path / "benchmarks")
        assert cost.trace_timestamp == "2026-03-31T22:14:36.052Z"

    def test_timestamp_is_the_max_not_the_last_line(self, tmp_path: Path) -> None:
        """Real traces end on a line with no timestamp at all.

        Verified against the corpus: 28 of 29 lines in
        results/mcp_batch/api-contract-grpc-metadata-001_hybrid/agent_trace.jsonl
        carry a timestamp; the trailing ``last-prompt`` line does not. A
        last-line rule would resolve most real runs to "" and silently collapse
        every tiebreak to run_dir.
        """
        task_dir = tmp_path / "mcp_batch" / "t1_hybrid"
        _write_trace(
            task_dir / "agent_trace.jsonl",
            [
                {"type": "queue-operation", "timestamp": "2026-03-31T22:14:36.052Z"},
                _assistant_entry() | {"timestamp": "2026-03-31T22:20:00.000Z"},
                {"type": "last-prompt", "lastPrompt": "...", "sessionId": "s"},
            ],
        )
        (cost,) = scan_results_dirs([tmp_path / "mcp_batch"], tmp_path / "benchmarks")
        assert cost.trace_timestamp == "2026-03-31T22:20:00.000Z"

    def test_timestamp_comes_from_non_assistant_lines_too(self, tmp_path: Path) -> None:
        """The usage loop skips non-assistant lines; the timestamp scan must not."""
        task_dir = tmp_path / "mcp_batch" / "t1_hybrid"
        _write_trace(
            task_dir / "agent_trace.jsonl",
            [
                _assistant_entry() | {"timestamp": "2026-01-01T00:00:00Z"},
                {"type": "queue-operation", "timestamp": "2026-09-09T00:00:00Z"},
            ],
        )
        (cost,) = scan_results_dirs([tmp_path / "mcp_batch"], tmp_path / "benchmarks")
        assert cost.trace_timestamp == "2026-09-09T00:00:00Z"

    def test_trace_with_no_timestamps_at_all_is_empty_not_an_error(
        self, tmp_path: Path
    ) -> None:
        _write_attempt(tmp_path, "mcp_batch", "t1", "hybrid", timestamp=None)
        (cost,) = scan_results_dirs([tmp_path / "mcp_batch"], tmp_path / "benchmarks")
        assert cost.trace_timestamp == ""

    def test_malformed_results_json_is_invalid_not_a_crash(self, tmp_path: Path) -> None:
        """A run that crashed mid-write leaves truncated JSON, not valid JSON."""
        task_dir = _write_attempt(tmp_path, "mcp_batch", "t1", "hybrid", score=None)
        (task_dir / "results.json").write_text('{"task_id": "t1", "scor')
        (cost,) = scan_results_dirs([tmp_path / "mcp_batch"], tmp_path / "benchmarks")
        assert cost.normalized_score is None

    def test_zero_checkpoints_is_invalid_not_a_zero_score(self, tmp_path: Path) -> None:
        """analyze_scores refuses to divide by zero here; so must the cost side.

        Treating it as 0.0 would make an unscorable run eligible to represent
        its cell, and it would lose every tiebreak to a real run only by luck.
        """
        task_dir = _write_attempt(tmp_path, "mcp_batch", "t1", "hybrid", score=None)
        (task_dir / "results.json").write_text(
            json.dumps(
                {
                    "task_id": "t1",
                    "scores": {"checkpoints_total": 0, "task_score": 0.0},
                    "config": {"mode": "hybrid"},
                }
            )
        )
        (cost,) = scan_results_dirs([tmp_path / "mcp_batch"], tmp_path / "benchmarks")
        assert cost.normalized_score is None

    def test_attempt_without_results_json_is_invalid_not_zero(
        self, tmp_path: Path
    ) -> None:
        _write_attempt(tmp_path, "mcp_batch", "t1", "hybrid", score=None)
        (cost,) = scan_results_dirs([tmp_path / "mcp_batch"], tmp_path / "benchmarks")
        assert cost.normalized_score is None
        assert cost.cost_usd > 0  # it still SPENT money; it just cannot be compared


class TestSelectAttempt:
    """Which attempt represents a (task_id, mode) cell, and why that rule.

    The rule must match ``analyze_scores._load_results`` (highest normalized
    score), or the cost row and the score row of the same cell describe
    different runs and every score-vs-cost join is wrong.
    """

    def test_highest_score_wins(self) -> None:
        low = _make_cost(run_dir="a", normalized_score=0.25, cost_usd=9.0)
        high = _make_cost(run_dir="b", normalized_score=0.75, cost_usd=1.0)
        assert select_attempt([low, high]) is high

    def test_ties_break_on_latest_trace_timestamp(self) -> None:
        old = _make_cost(
            run_dir="a", normalized_score=0.5, trace_timestamp="2026-01-01T00:00:00Z"
        )
        new = _make_cost(
            run_dir="b", normalized_score=0.5, trace_timestamp="2026-06-01T00:00:00Z"
        )
        assert select_attempt([old, new]) is new
        assert select_attempt([new, old]) is new

    def test_selection_is_independent_of_input_order_and_mtime(self) -> None:
        """The rule is content-derived, so shuffling the scan order cannot move it.

        mtime would not survive a clone or a rescore pass; the trace timestamp and
        the score both live in the artifacts.
        """
        attempts = [
            _make_cost(run_dir=f"r{i}", normalized_score=s, trace_timestamp=ts)
            for i, (s, ts) in enumerate(
                [(0.5, "2026-01-01T00:00:00Z"), (0.9, "2026-01-01T00:00:00Z")]
            )
        ]
        assert select_attempt(attempts).run_dir == "r1"
        assert select_attempt(list(reversed(attempts))).run_dir == "r1"

    def test_fully_tied_attempts_break_on_run_dir_deterministically(self) -> None:
        a = _make_cost(run_dir="aaa", normalized_score=0.5, trace_timestamp="T")
        b = _make_cost(run_dir="bbb", normalized_score=0.5, trace_timestamp="T")
        assert select_attempt([a, b]) is b
        assert select_attempt([b, a]) is b

    def test_all_invalid_selects_nothing(self) -> None:
        assert select_attempt([_make_cost(normalized_score=None)]) is None

    def test_invalid_attempt_never_outranks_a_scored_one(self) -> None:
        scored = _make_cost(run_dir="a", normalized_score=0.0)
        unscored = _make_cost(run_dir="z", normalized_score=None)
        assert select_attempt([scored, unscored]) is scored


class TestTwoViewsAreNotCollapsed:
    """The policy: total spend and suite cost are two numbers, never one.

    Operational economics sums every attempt (what was actually paid).
    Comparison economics bills one attempt per (task_id, mode). Publishing a
    single ambiguous total is what this bead exists to stop.
    """

    def test_operational_sums_every_attempt(self) -> None:
        report = aggregate_report(
            [
                _make_cost(task_id="t1", mode="hybrid", run_dir="a", cost_usd=1.0),
                _make_cost(task_id="t1", mode="hybrid", run_dir="b", cost_usd=2.0),
            ]
        )
        assert report["operational_economics"]["total_cost_usd"] == 3.0
        assert report["operational_economics"]["attempts"] == 2

    def test_comparison_bills_one_attempt_per_cell(self) -> None:
        report = aggregate_report(
            [
                _make_cost(
                    task_id="t1", mode="hybrid", run_dir="a",
                    cost_usd=1.0, normalized_score=0.2,
                ),
                _make_cost(
                    task_id="t1", mode="hybrid", run_dir="b",
                    cost_usd=2.0, normalized_score=0.9,
                ),
            ]
        )
        comp = report["comparison_economics"]
        assert comp["tasks"] == 1
        assert comp["total_cost_usd"] == 2.0  # the selected attempt, not the sum

    def test_the_two_totals_actually_differ_on_a_rerun_corpus(self) -> None:
        """Guards the implementation that returns the attempt total under both names."""
        report = aggregate_report(
            [
                _make_cost(task_id="t1", mode="hybrid", run_dir=f"r{i}",
                           cost_usd=1.0, normalized_score=0.5)
                for i in range(3)
            ]
        )
        assert report["operational_economics"]["total_cost_usd"] == 3.0
        assert report["comparison_economics"]["total_cost_usd"] == 1.0
        assert report["operational_economics"]["attempts"] == 3
        assert report["comparison_economics"]["tasks"] == 1

    def test_denominators_are_named_per_view(self) -> None:
        """No field's meaning may depend on which block it sits in."""
        report = aggregate_report(
            [_make_cost(task_id="t1", mode="hybrid", run_dir="a", cost_usd=2.0)]
        )
        op = report["operational_economics"]["by_mode"]["hybrid"]
        comp = report["comparison_economics"]["by_mode"]["hybrid"]
        assert {"attempts", "avg_cost_per_attempt"} <= op.keys()
        assert "avg_cost" not in op and "count" not in op
        assert {"tasks", "avg_cost_per_task"} <= comp.keys()
        assert "avg_cost" not in comp and "count" not in comp

    def test_cross_arm_average_does_not_reuse_the_per_arm_key(self) -> None:
        """Same rule one level down: by_suite sums arms, by_mode does not.

        Within an arm, ``avg_cost_per_task`` is what one task cost in that arm.
        A suite bucket sums every arm, so its average is what one task cost
        across the whole sweep — a different number answering a different
        question. Sharing the key would put the meaning back inside the block,
        which is exactly what the test above forbids one level up.
        """
        report = aggregate_report(
            [
                _make_cost(task_id="t1", mode=m, run_dir=f"a-{m}", cost_usd=2.0)
                for m in ("baseline", "hybrid")
            ]
        )
        comp = report["comparison_economics"]
        by_mode = comp["by_mode"]["baseline"]
        by_suite = comp["by_suite"]["customer_escalation"]

        assert by_mode["avg_cost_per_task"] == 2.0
        assert "avg_cost_per_task_across_arms" not in by_mode

        assert by_suite["avg_cost_per_task_across_arms"] == 4.0
        assert "avg_cost_per_task" not in by_suite

    def test_comparison_avg_divides_by_distinct_tasks(self) -> None:
        """Re-run-heavy arms must not be weighted differently from clean ones."""
        report = aggregate_report(
            [
                _make_cost(task_id="t1", mode="hybrid", run_dir="a", cost_usd=2.0),
                _make_cost(task_id="t1", mode="hybrid", run_dir="b", cost_usd=2.0),
                _make_cost(task_id="t2", mode="hybrid", run_dir="c", cost_usd=4.0),
            ]
        )
        by_mode = report["comparison_economics"]["by_mode"]["hybrid"]
        assert by_mode["tasks"] == 2
        assert by_mode["avg_cost_per_task"] == 3.0
        # The attempt view keeps the honest per-attempt figure: 8.0 / 3.
        op = report["operational_economics"]["by_mode"]["hybrid"]
        assert op["attempts"] == 3
        assert op["avg_cost_per_attempt"] == round(8.0 / 3, 6)


class TestComparisonIsMatchedAcrossArms:
    """Arm totals over different task sets are not comparable, so they are not built.

    Policy clause 1: paired by task and arm. A task missing from an arm is
    excluded from every arm and named.
    """

    def test_unmatched_task_is_excluded_from_all_arms(self) -> None:
        report = aggregate_report(
            [
                _make_cost(task_id="both", mode="hybrid", run_dir="a", cost_usd=1.0),
                _make_cost(task_id="both", mode="mcp_only", run_dir="b", cost_usd=2.0),
                _make_cost(task_id="lonely", mode="hybrid", run_dir="c", cost_usd=8.0),
            ]
        )
        comp = report["comparison_economics"]
        assert comp["excluded_unmatched_task_ids"] == ["lonely"]
        assert comp["total_cost_usd"] == 3.0  # 8.0 dropped from BOTH arms
        assert comp["by_mode"]["hybrid"]["tasks"] == 1
        assert comp["by_mode"]["mcp_only"]["tasks"] == 1

    def test_excluded_task_still_counts_as_spend(self) -> None:
        report = aggregate_report(
            [
                _make_cost(task_id="both", mode="hybrid", run_dir="a", cost_usd=1.0),
                _make_cost(task_id="both", mode="mcp_only", run_dir="b", cost_usd=2.0),
                _make_cost(task_id="lonely", mode="hybrid", run_dir="c", cost_usd=8.0),
            ]
        )
        assert report["operational_economics"]["total_cost_usd"] == 11.0

    def test_three_arms_require_presence_in_all_three(self) -> None:
        """"Matched" means every arm seen, not merely more than one.

        Production runs up to four arms. A task in two of three is not paired.
        """
        report = aggregate_report(
            [
                _make_cost(task_id="all3", mode=m, run_dir=f"a-{m}", cost_usd=1.0)
                for m in ("baseline", "mcp_only", "hybrid")
            ]
            + [
                _make_cost(task_id="only2", mode=m, run_dir=f"b-{m}", cost_usd=5.0)
                for m in ("baseline", "hybrid")
            ]
        )
        comp = report["comparison_economics"]
        assert comp["modes"] == ["baseline", "hybrid", "mcp_only"]
        assert comp["excluded_unmatched_task_ids"] == ["only2"]
        # One task, matched in three arms. The count is TASKS, not the three
        # (task_id, mode) cells that carry them — publishing 3 here is what put
        # "restricted to the 3 tasks present in every arm" into the report prose
        # and "3 tasks matched across arms" into a published PNG title.
        assert comp["tasks"] == 1
        assert comp["total_cost_usd"] == 3.0
        assert len(comp["per_cell"]) == 3

    def test_single_arm_corpus_matches_everything(self) -> None:
        report = aggregate_report(
            [
                _make_cost(task_id="t1", mode="hybrid", run_dir="a", cost_usd=1.0),
                _make_cost(task_id="t2", mode="hybrid", run_dir="b", cost_usd=2.0),
            ]
        )
        comp = report["comparison_economics"]
        assert comp["excluded_unmatched_task_ids"] == []
        assert comp["tasks"] == 2

    def test_non_arm_mode_is_operational_only(self) -> None:
        """A directory whose mode could not be parsed must not define an arm."""
        report = aggregate_report(
            [
                _make_cost(task_id="t1", mode="hybrid", run_dir="a", cost_usd=1.0),
                _make_cost(task_id="t2", mode="unknown", run_dir="b", cost_usd=5.0),
            ]
        )
        assert report["operational_economics"]["total_cost_usd"] == 6.0
        assert report["comparison_economics"]["modes"] == ["hybrid"]
        assert report["comparison_economics"]["total_cost_usd"] == 1.0


class TestComparisonDenominatorIsTasks:
    """Every count in the comparison view is TASKS, in every dimension.

    ``by_mode`` was correct by accident — within one arm a (task_id, mode) cell
    IS a task — which is what let the cell count masquerade as a task count
    everywhere else. ``by_suite``/``by_difficulty`` sum across arms, so there
    the two diverge by a factor of the arm count: on a four-arm sweep a suite
    with one task reported four.
    """

    def _two_arms_two_suites(self) -> dict[str, Any]:
        return aggregate_report(
            [
                _make_cost(
                    task_id=task, mode=mode, suite=suite,
                    run_dir=f"{task}-{mode}", cost_usd=1.0,
                )
                for task, suite in (("esc1", "customer_escalation"), ("dep1", "dep_traversal"))
                for mode in ("baseline", "hybrid")
            ]
        )

    def test_suite_bucket_counts_tasks_not_cells(self) -> None:
        by_suite = self._two_arms_two_suites()["comparison_economics"]["by_suite"]
        assert by_suite["customer_escalation"]["tasks"] == 1
        assert by_suite["dep_traversal"]["tasks"] == 1

    def test_difficulty_bucket_counts_tasks_not_cells(self) -> None:
        by_diff = self._two_arms_two_suites()["comparison_economics"]["by_difficulty"]
        assert by_diff["medium"]["tasks"] == 2

    def test_dimension_counts_sum_to_the_headline_count(self) -> None:
        """The invariant a reader relies on to trust any breakdown at all."""
        comp = self._two_arms_two_suites()["comparison_economics"]
        assert comp["tasks"] == 2
        assert sum(b["tasks"] for b in comp["by_suite"].values()) == comp["tasks"]
        assert sum(b["tasks"] for b in comp["by_difficulty"].values()) == comp["tasks"]

    def test_every_arm_ran_every_matched_task(self) -> None:
        """Matching is what makes this true; by_mode must show it, not assume it."""
        comp = self._two_arms_two_suites()["comparison_economics"]
        assert all(b["tasks"] == comp["tasks"] for b in comp["by_mode"].values())

    def test_cross_arm_average_divides_by_distinct_tasks(self) -> None:
        by_suite = self._two_arms_two_suites()["comparison_economics"]["by_suite"]
        # $1 per cell x 2 arms over 1 task. Dividing by the 2 cells would report
        # $1.00 and read as "this suite's tasks cost a dollar each".
        assert by_suite["customer_escalation"]["total_cost_usd"] == 2.0
        assert by_suite["customer_escalation"]["avg_cost_per_task_across_arms"] == 2.0

    def test_per_cell_is_one_row_per_task_and_arm(self) -> None:
        """The row list is cells; only its NAME ever claimed otherwise.

        Each row carries its own mode and run_dir, and generate_charts keys the
        list on (task_id, mode) for that reason. A reader who took ``len()`` as
        a task count got the right answer only while ``tasks`` was equally wrong.
        """
        comp = self._two_arms_two_suites()["comparison_economics"]
        assert len(comp["per_cell"]) == comp["tasks"] * len(comp["modes"])
        assert {(r["task_id"], r["mode"]) for r in comp["per_cell"]} == {
            (task, mode)
            for task in ("esc1", "dep1")
            for mode in ("baseline", "hybrid")
        }


class TestArmSetSurvivesAnUnscoredArm:
    """An arm that scored nothing must keep its seat and empty the comparison.

    Deriving the arm set from the SCORED cells lets a wholly-failed arm drop out
    of the intersection, so the survivors match each other perfectly and the
    report reads as a complete comparison with an arm missing. The failure is
    silent in both directions: nothing says the arm is gone, and nothing says
    the remaining numbers cover fewer arms than the run did.
    """

    @staticmethod
    def _one_arm_scored_nothing() -> dict[str, Any]:
        return aggregate_report(
            [
                _make_cost(task_id=task, mode="baseline", run_dir=f"{task}-b",
                           cost_usd=1.0, normalized_score=0.5)
                for task in ("t1", "t2")
            ]
            + [
                _make_cost(task_id=task, mode="mcp_only", run_dir=f"{task}-m",
                           cost_usd=1.0, normalized_score=None)
                for task in ("t1", "t2")
            ]
        )

    def test_dead_arm_is_still_named(self) -> None:
        comp = self._one_arm_scored_nothing()["comparison_economics"]
        assert comp["modes"] == ["baseline", "mcp_only"]

    def test_nothing_matches_when_an_arm_scored_nothing(self) -> None:
        comp = self._one_arm_scored_nothing()["comparison_economics"]
        assert comp["tasks"] == 0
        assert comp["total_cost_usd"] == 0.0
        assert comp["by_mode"] == {}

    def test_every_task_is_named_as_excluded(self) -> None:
        comp = self._one_arm_scored_nothing()["comparison_economics"]
        assert comp["excluded_unmatched_task_ids"] == ["t1", "t2"]

    def test_the_spend_is_still_reported(self) -> None:
        """The comparison view empties; the money does not disappear with it."""
        op = self._one_arm_scored_nothing()["operational_economics"]
        assert op["total_cost_usd"] == 4.0
        assert op["attempts"] == 4

    def test_an_unparseable_mode_still_defines_no_arm(self) -> None:
        """Widening the arm source must not widen it to non-arms.

        "unknown" is a directory name that would not parse, not a fourth arm.
        Letting it in would collapse every intersection to nothing.
        """
        comp = aggregate_report(
            [
                _make_cost(task_id="t1", mode="hybrid", run_dir="a", cost_usd=1.0),
                _make_cost(task_id="t1", mode="unknown", run_dir="b",
                           cost_usd=5.0, normalized_score=None),
            ]
        )["comparison_economics"]
        assert comp["modes"] == ["hybrid"]
        assert comp["tasks"] == 1


class TestAttemptsAreFullyEnumerated:
    """Policy: invalid and retry attempts are excluded from ratios but enumerated."""

    def test_duplicates_are_listed_with_the_selection_named(self) -> None:
        report = aggregate_report(
            [
                _make_cost(task_id="t1", mode="hybrid", run_dir="a",
                           cost_usd=1.0, normalized_score=0.2),
                _make_cost(task_id="t1", mode="hybrid", run_dir="b",
                           cost_usd=2.0, normalized_score=0.9),
            ]
        )
        (dup,) = report["duplicate_attempts"]
        assert dup["task_id"] == "t1"
        assert dup["mode"] == "hybrid"
        assert dup["attempts"] == 2
        assert [r["run_dir"] for r in dup["runs"]] == ["a", "b"]
        assert [r["selected"] for r in dup["runs"]] == [False, True]

    def test_single_attempt_cells_are_not_listed_as_duplicates(self) -> None:
        report = aggregate_report([_make_cost(task_id="t1", run_dir="a")])
        assert report["duplicate_attempts"] == []

    def test_duplicate_runs_are_ordered_by_run_dir(self) -> None:
        """Pinned explicitly: score order and insertion order both disagree here,
        so a passing implementation cannot be relying on either by accident."""
        report = aggregate_report(
            [
                _make_cost(task_id="t1", run_dir="z-last",
                           cost_usd=1.0, normalized_score=0.9),
                _make_cost(task_id="t1", run_dir="a-first",
                           cost_usd=2.0, normalized_score=0.1),
            ]
        )
        (dup,) = report["duplicate_attempts"]
        assert [r["run_dir"] for r in dup["runs"]] == ["a-first", "z-last"]
        assert [r["selected"] for r in dup["runs"]] == [False, True]


class TestEveryDollarIsAccountedFor:
    """Conservation: no attempt's cost may vanish between the two views.

    The policy's "fully enumerated" clause is only meaningful if the gap between
    total spend and matched suite cost can be itemized. ``per_attempt`` is the
    complete ledger, and the comparison view is a strict subset of it — an
    attempt dropped from both, or double-counted in one, breaks these.
    """

    def _mixed_corpus(self) -> list[TaskCost]:
        return [
            # matched, re-run: one attempt selected, one not
            _make_cost(task_id="both", mode="hybrid", run_dir="h1",
                       cost_usd=1.0, normalized_score=0.9),
            _make_cost(task_id="both", mode="hybrid", run_dir="h2",
                       cost_usd=2.0, normalized_score=0.1),
            _make_cost(task_id="both", mode="baseline", run_dir="b1",
                       cost_usd=4.0, normalized_score=0.5),
            # unmatched: present in one arm only
            _make_cost(task_id="lonely", mode="hybrid", run_dir="l1",
                       cost_usd=8.0, normalized_score=0.7),
            # unscored
            _make_cost(task_id="both", mode="baseline", run_dir="b2",
                       cost_usd=16.0, normalized_score=None),
            # non-arm mode
            _make_cost(task_id="weird", mode="unknown", run_dir="u1",
                       cost_usd=32.0, normalized_score=0.4),
        ]

    def test_per_attempt_is_the_complete_ledger(self) -> None:
        costs = self._mixed_corpus()
        report = aggregate_report(costs)
        rows = report["per_attempt"]
        assert len(rows) == len(costs)
        assert {r["run_dir"] for r in rows} == {c.run_dir for c in costs}
        assert round(sum(r["cost_usd"] for r in rows), 6) == (
            report["operational_economics"]["total_cost_usd"]
        )

    def test_the_gap_between_the_views_is_itemizable(self) -> None:
        report = aggregate_report(self._mixed_corpus())
        op_total = report["operational_economics"]["total_cost_usd"]
        comp_total = report["comparison_economics"]["total_cost_usd"]

        selected = {r["run_dir"] for r in report["comparison_economics"]["per_cell"]}
        unselected = [
            r for r in report["per_attempt"] if r["run_dir"] not in selected
        ]
        assert round(comp_total + sum(r["cost_usd"] for r in unselected), 6) == op_total

    def test_comparison_rows_are_a_strict_subset_of_the_ledger(self) -> None:
        report = aggregate_report(self._mixed_corpus())
        ledger = {r["run_dir"] for r in report["per_attempt"]}
        selected = [r["run_dir"] for r in report["comparison_economics"]["per_cell"]]
        assert len(selected) == len(set(selected))  # no attempt billed twice
        assert set(selected) < ledger

    def test_every_excluded_attempt_is_locatable_in_the_ledger(self) -> None:
        """Whatever the exclusion reason — re-run, unscored, unmatched, non-arm —
        the attempt is findable with its run_dir and its cost."""
        report = aggregate_report(self._mixed_corpus())
        by_run = {r["run_dir"]: r for r in report["per_attempt"]}
        for run_dir in ("h2", "l1", "b2", "u1"):
            assert by_run[run_dir]["cost_usd"] > 0
            assert by_run[run_dir]["mode"]

    def test_invalid_attempts_are_enumerated(self) -> None:
        report = aggregate_report(
            [
                _make_cost(task_id="t1", mode="hybrid", run_dir="ok"),
                _make_cost(task_id="t2", mode="hybrid", run_dir="bad",
                           cost_usd=3.0, normalized_score=None),
            ]
        )
        (bad,) = report["invalid_attempts"]
        assert bad["task_id"] == "t2"
        assert bad["run_dir"] == "bad"
        assert bad["cost_usd"] == 3.0

    def test_invalid_attempt_is_spend_but_never_a_comparison_row(self) -> None:
        report = aggregate_report(
            [
                _make_cost(task_id="t1", mode="hybrid", run_dir="ok", cost_usd=1.0),
                _make_cost(task_id="t1", mode="hybrid", run_dir="bad",
                           cost_usd=3.0, normalized_score=None),
            ]
        )
        assert report["operational_economics"]["total_cost_usd"] == 4.0
        assert report["comparison_economics"]["total_cost_usd"] == 1.0

    def test_cell_with_only_invalid_attempts_has_no_comparison_row(self) -> None:
        report = aggregate_report(
            [_make_cost(task_id="t1", mode="hybrid", run_dir="bad",
                        normalized_score=None)]
        )
        assert report["comparison_economics"]["tasks"] == 0
        assert report["operational_economics"]["attempts"] == 1

    def test_a_never_scored_task_is_invalid_not_unmatched(self) -> None:
        """One exclusion, one list, one accurate label.

        ``excluded_unmatched_task_ids`` is rendered as "not present in every
        arm". A task nothing ever scored WAS present; the scoring layer produced
        nothing for it. Listing it there would state something false about it and
        report it twice, since ``invalid_attempts`` already carries every
        unscored attempt with its run_dir and cost.
        """
        report = aggregate_report(
            [_make_cost(task_id="t1", mode="hybrid", run_dir="bad",
                        normalized_score=None)]
        )
        assert report["comparison_economics"]["excluded_unmatched_task_ids"] == []
        assert [r["run_dir"] for r in report["invalid_attempts"]] == ["bad"]

    def test_the_same_holds_when_it_ran_in_only_one_arm(self) -> None:
        """Absence from the excluded list is about scoring, not about coverage.

        "ghost" ran in one arm of two, so it was genuinely not present in every
        arm — yet it still does not belong in a list that means "some arm scored
        this and another did not". Nothing scored it anywhere, so it never
        entered the matching to be excluded from it.
        """
        report = aggregate_report(
            [
                _make_cost(task_id="real", mode=m, run_dir=f"r-{m}",
                           normalized_score=0.5)
                for m in ("baseline", "mcp_only")
            ]
            + [
                _make_cost(task_id="ghost", mode="baseline", run_dir="g-b",
                           normalized_score=None),
            ]
        )
        comp = report["comparison_economics"]
        assert comp["excluded_unmatched_task_ids"] == []
        assert comp["tasks"] == 1
        assert [r["task_id"] for r in report["invalid_attempts"]] == ["ghost"]


class TestReportSchemaGuard:
    """A stale report must fail loudly, not render $0.00.

    Every consumer reads with ``.get(key, 0)``. Feed yesterday's JSON to today's
    reader and it prints a plausible zero — the same failure class
    ``scorer_guard.py`` exists to forbid.
    """

    def test_report_declares_its_schema_version(self) -> None:
        assert aggregate_report([])["schema_version"] == SCHEMA_VERSION

    def test_report_records_the_selection_rule(self) -> None:
        """Named in the JSON, not merely non-empty — a reader must not have to
        infer which of several plausible rules produced the comparison view."""
        rule = aggregate_report([])["selection_rule"]
        assert "normalized_score" in rule
        assert "timestamp" in rule
        assert "run_dir" in rule

    def test_require_schema_accepts_a_current_report(self) -> None:
        require_schema(aggregate_report([]), "test")

    def test_require_schema_rejects_a_report_with_no_version(self) -> None:
        with pytest.raises(ValueError, match="schema_version"):
            require_schema({"total_cost_usd": 12.0}, "test")

    def test_require_schema_rejects_an_older_version(self) -> None:
        with pytest.raises(ValueError, match="schema_version"):
            require_schema({"schema_version": SCHEMA_VERSION - 1}, "test")


class TestAgreesWithAnalyzeScores:
    """The cost row and the score row of a cell must describe the SAME run.

    This is the whole reason ``select_attempt`` reuses analyze_scores' key rather
    than inventing one. A test that only checks cost_tracker in isolation would
    not notice the two modules drifting apart.
    """

    def _corpus(self, tmp_path: Path, scores: list[tuple[str, float]]) -> list[Path]:
        for batch, score in scores:
            _write_attempt(
                tmp_path, batch, "t1", "hybrid",
                score=score, timestamp=f"2026-0{len(batch) % 9 + 1}-01T00:00:00Z",
            )
        return [tmp_path / batch for batch, _ in scores]

    def test_both_modules_pick_the_same_run(self, tmp_path: Path) -> None:
        dirs = self._corpus(
            tmp_path, [("mcp_batch", 0.9), ("mcp_batch_v2", 0.1), ("mcp_batch_v3", 0.4)]
        )
        bench = tmp_path / "benchmarks"

        (scored,) = load_all_results(dirs, bench)
        report = aggregate_report(scan_results_dirs(dirs, bench))
        (billed,) = report["comparison_economics"]["per_cell"]

        # source_path is the results.json; run_dir is its directory.
        assert str(Path(scored.source_path).parent) == billed["run_dir"]
        assert scored.normalized_score == billed["normalized_score"]

    def test_they_agree_when_the_newest_run_scored_worst(self, tmp_path: Path) -> None:
        """The case a naive latest-attempt rule gets wrong."""
        dirs = self._corpus(tmp_path, [("mcp_batch", 0.9), ("mcp_batch_v2", 0.1)])
        bench = tmp_path / "benchmarks"

        (scored,) = load_all_results(dirs, bench)
        (billed,) = aggregate_report(scan_results_dirs(dirs, bench))[
            "comparison_economics"
        ]["per_cell"]
        assert str(Path(scored.source_path).parent) == billed["run_dir"]
        assert billed["normalized_score"] == 0.9


class TestEndToEndReruns:
    """The measured bug, end to end: more attempt rows than distinct cells."""

    def test_rerun_across_batches_inflates_only_the_operational_total(
        self, tmp_path: Path
    ) -> None:
        # Directory sort order (mcp_batch < mcp_batch_v2) OPPOSES the intended
        # selection, so a lexical-order or rglob-order fallback fails this test.
        _write_attempt(
            tmp_path, "mcp_batch", "t1", "hybrid",
            score=0.9, timestamp="2026-01-01T00:00:00Z",
        )
        _write_attempt(
            tmp_path, "mcp_batch_v2", "t1", "hybrid",
            score=0.1, timestamp="2026-06-01T00:00:00Z",
        )
        costs = scan_results_dirs(
            [tmp_path / "mcp_batch", tmp_path / "mcp_batch_v2"],
            tmp_path / "benchmarks",
        )
        report = aggregate_report(costs)

        assert report["operational_economics"]["attempts"] == 2
        assert report["comparison_economics"]["tasks"] == 1
        assert (
            report["operational_economics"]["total_cost_usd"]
            > report["comparison_economics"]["total_cost_usd"]
        )
        (dup,) = report["duplicate_attempts"]
        selected = [r for r in dup["runs"] if r["selected"]]
        assert len(selected) == 1
        assert "mcp_batch/" in selected[0]["run_dir"]  # the 0.9 run, not the newer 0.1

    def test_selection_survives_identical_mtimes(self, tmp_path: Path) -> None:
        """Proves the rule is content-derived: mtime carries no information here.

        The winning attempt is deliberately the lexically *earlier* run_dir with
        the *equal* timestamp, so neither tiebreaker can explain the result — only
        the score can. An implementation that fell back to mtime or max(run_dir)
        would pick the 0.2 run and fail.
        """
        dirs = [
            _write_attempt(tmp_path, "mcp_batch", "t1", "hybrid",
                           score=0.8, timestamp="2026-01-01T00:00:00Z"),
            _write_attempt(tmp_path, "mcp_batch_v2", "t1", "hybrid",
                           score=0.2, timestamp="2026-01-01T00:00:00Z"),
        ]
        for d in dirs:
            for f in d.iterdir():
                os.utime(f, (1_000_000, 1_000_000))

        costs = scan_results_dirs(
            [tmp_path / "mcp_batch", tmp_path / "mcp_batch_v2"],
            tmp_path / "benchmarks",
        )
        report = aggregate_report(costs)
        (dup,) = report["duplicate_attempts"]
        (selected,) = [r for r in dup["runs"] if r["selected"]]
        assert selected["normalized_score"] == 0.8

    def test_per_attempt_rows_are_one_per_attempt(self, tmp_path: Path) -> None:
        _write_attempt(tmp_path, "mcp_batch", "t1", "hybrid")
        _write_attempt(tmp_path, "mcp_batch_v2", "t1", "hybrid")
        costs = scan_results_dirs(
            [tmp_path / "mcp_batch", tmp_path / "mcp_batch_v2"],
            tmp_path / "benchmarks",
        )
        report = aggregate_report(costs)
        assert len(report["per_attempt"]) == 2
        assert len(report["comparison_economics"]["per_cell"]) == 1
        assert {"run_dir", "normalized_score", "trace_timestamp"} <= set(
            report["per_attempt"][0]
        )
