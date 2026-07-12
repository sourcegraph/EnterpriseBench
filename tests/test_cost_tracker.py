"""Tests for scripts/cost_tracker.py."""

from __future__ import annotations

import json
import sys
from pathlib import Path


# Make scripts importable
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))

from cost_tracker import (
    DEFAULT_MODEL,
    TaskCost,
    TraceUsage,
    aggregate_report,
    compute_cost,
    parse_trace,
    scan_results_dirs,
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


# ---------------------------------------------------------------------------
# parse_trace
# ---------------------------------------------------------------------------


class TestParseTrace:
    def test_single_assistant_message(self, tmp_path: Path) -> None:
        trace = _write_trace(
            tmp_path / "agent_trace.jsonl",
            [_assistant_entry(input_tokens=200, output_tokens=80)],
        )
        usage = parse_trace(trace)
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
        usage = parse_trace(trace)
        assert usage.input_tokens == 300
        assert usage.output_tokens == 80
        assert usage.cache_write_tokens == 10
        assert usage.cache_read_tokens == 500
        assert usage.num_requests == 2

    def test_empty_trace(self, tmp_path: Path) -> None:
        trace = _write_trace(tmp_path / "agent_trace.jsonl", [])
        usage = parse_trace(trace)
        assert usage.input_tokens == 0
        assert usage.num_requests == 0
        assert usage.model == DEFAULT_MODEL

    def test_malformed_lines_skipped(self, tmp_path: Path) -> None:
        path = tmp_path / "agent_trace.jsonl"
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("w") as fh:
            fh.write("not valid json\n")
            fh.write(
                json.dumps(_assistant_entry(input_tokens=42, output_tokens=7)) + "\n"
            )
        usage = parse_trace(path)
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
        usage = parse_trace(trace)
        assert usage.num_requests == 0

    def test_model_captured_from_first_assistant(self, tmp_path: Path) -> None:
        trace = _write_trace(
            tmp_path / "agent_trace.jsonl",
            [
                _assistant_entry(model="claude-opus-4-6"),
                _assistant_entry(model="claude-haiku-4-5"),
            ],
        )
        usage = parse_trace(trace)
        assert usage.model == "claude-opus-4-6"


# ---------------------------------------------------------------------------
# parse_trace — per-request dedup (EnterpriseBench-ewr8)
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
        usage = parse_trace(trace)

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
        usage = parse_trace(trace)

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
        usage = parse_trace(trace)

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
        usage = parse_trace(trace)

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
        usage = parse_trace(trace)

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
        usage = parse_trace(trace)

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
        usage = parse_trace(trace)

        assert usage.input_tokens == 300
        assert usage.output_tokens == 80
        assert usage.num_requests == 2


# ---------------------------------------------------------------------------
# compute_cost
# ---------------------------------------------------------------------------


class TestComputeCost:
    def test_sonnet_known_values(self) -> None:
        usage = TraceUsage(
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
        usage = TraceUsage(
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
        usage = TraceUsage(
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

    def test_model_override(self) -> None:
        usage = TraceUsage(
            input_tokens=1_000_000,
            output_tokens=0,
            cache_write_tokens=0,
            cache_read_tokens=0,
            model="claude-sonnet-4-6",
            num_requests=1,
        )
        cost_sonnet = compute_cost(usage)
        cost_opus = compute_cost(usage, model="claude-opus-4-6")
        assert cost_sonnet == 3.0
        assert cost_opus == 15.0

    def test_unknown_model_falls_back_to_default(self) -> None:
        usage = TraceUsage(
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
        usage = TraceUsage(
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
        task_id, mode = _parse_dir_identity(p)
        assert task_id == "dep-traversal-001"
        assert mode == "baseline"

    def test_mcp_only(self, tmp_path: Path) -> None:
        p = tmp_path / "mcp_batch" / "dep-traversal-001_mcp_only"
        task_id, mode = _parse_dir_identity(p)
        assert task_id == "dep-traversal-001"
        assert mode == "mcp_only"

    def test_hybrid(self, tmp_path: Path) -> None:
        p = tmp_path / "mcp_batch_v2" / "cal-drift-flask-config-001_hybrid"
        task_id, mode = _parse_dir_identity(p)
        assert task_id == "cal-drift-flask-config-001"
        assert mode == "hybrid"

    def test_no_mode_suffix(self, tmp_path: Path) -> None:
        p = tmp_path / "mcp_batch" / "some-task-name"
        task_id, mode = _parse_dir_identity(p)
        assert task_id == "some-task-name"
        assert mode == "unknown"


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
) -> TaskCost:
    """Build a TaskCost record for aggregate_report tests."""
    return TaskCost(
        task_id=task_id,
        mode=mode,
        suite=suite,
        difficulty=difficulty,
        usage=TraceUsage(
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            cache_write_tokens=0,
            cache_read_tokens=0,
            model=model,
            num_requests=3,
        ),
        cost_usd=cost_usd,
        agent_duration_seconds=60.0,
    )


class TestAggregateReport:
    def test_report_structure(self) -> None:
        costs = [_make_cost()]
        report = aggregate_report(costs)
        assert "generated_at" in report
        assert report["total_tasks"] == 1
        assert report["total_cost_usd"] == 0.01
        assert "by_mode" in report
        assert "by_suite" in report
        assert "by_difficulty" in report
        assert len(report["per_task"]) == 1

    def test_mode_breakdown(self) -> None:
        costs = [
            _make_cost(task_id="t1", mode="hybrid", cost_usd=1.0),
            _make_cost(task_id="t2", mode="hybrid", cost_usd=2.0),
            _make_cost(task_id="t3", mode="mcp_only", cost_usd=0.5),
        ]
        report = aggregate_report(costs)
        assert report["by_mode"]["hybrid"]["count"] == 2
        assert report["by_mode"]["hybrid"]["total_cost"] == 3.0
        assert report["by_mode"]["hybrid"]["avg_cost"] == 1.5
        assert report["by_mode"]["mcp_only"]["count"] == 1

    def test_suite_breakdown(self) -> None:
        costs = [
            _make_cost(task_id="t1", suite="incident_response"),
            _make_cost(task_id="t2", suite="incident_response"),
            _make_cost(task_id="t3", suite="feature_delivery"),
        ]
        report = aggregate_report(costs)
        assert report["by_suite"]["incident_response"]["count"] == 2
        assert report["by_suite"]["feature_delivery"]["count"] == 1

    def test_difficulty_breakdown(self) -> None:
        costs = [
            _make_cost(task_id="t1", difficulty="easy"),
            _make_cost(task_id="t2", difficulty="hard"),
        ]
        report = aggregate_report(costs)
        assert "easy" in report["by_difficulty"]
        assert "hard" in report["by_difficulty"]

    def test_empty_costs(self) -> None:
        report = aggregate_report([])
        assert report["total_tasks"] == 0
        assert report["total_cost_usd"] == 0.0
        assert report["per_task"] == []

    def test_per_task_sorted_by_id(self) -> None:
        costs = [
            _make_cost(task_id="z-task"),
            _make_cost(task_id="a-task"),
        ]
        report = aggregate_report(costs)
        assert report["per_task"][0]["task_id"] == "a-task"
        assert report["per_task"][1]["task_id"] == "z-task"

    def test_per_task_fields(self) -> None:
        costs = [_make_cost(input_tokens=999, output_tokens=111)]
        report = aggregate_report(costs)
        entry = report["per_task"][0]
        assert entry["input_tokens"] == 999
        assert entry["output_tokens"] == 111
        assert entry["model"] == "claude-sonnet-4-6"
        assert "cache_write_tokens" in entry
        assert "cache_read_tokens" in entry
        assert "agent_duration_seconds" in entry


class TestUnpricedModelDisclosure:
    """An unpriced model must be disclosed, never silently billed as sonnet.

    compute_cost falls back to DEFAULT_MODEL pricing for an unknown model. The
    unpriced models cluster in a single arm, so the fallback understates that
    arm's cost and corrupts the arm-to-arm cost delta — the exact comparison
    this report exists to support. The report has to carry the caveat with it.
    """

    def test_all_priced_reports_no_unpriced_models(self) -> None:
        report = aggregate_report([_make_cost(task_id="t1")])
        assert report["unpriced_models"] == []

    def test_unpriced_model_is_surfaced(self) -> None:
        report = aggregate_report(
            [
                _make_cost(task_id="t1", model="claude-sonnet-4-6"),
                _make_cost(task_id="t2", model="claude-opus-4-8"),
            ]
        )
        assert report["unpriced_models"] == ["claude-opus-4-8"]

    def test_unpriced_models_deduped_and_sorted(self) -> None:
        report = aggregate_report(
            [
                _make_cost(task_id="t1", model="claude-opus-4-8"),
                _make_cost(task_id="t2", model="claude-fable-5"),
                _make_cost(task_id="t3", model="claude-opus-4-8"),
            ]
        )
        assert report["unpriced_models"] == ["claude-fable-5", "claude-opus-4-8"]
