"""Tests for scripts/cost_tracker.py."""

from __future__ import annotations

import json
import math
import sys
from pathlib import Path

# Make scripts importable
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))

from cost_tracker import (
    DEFAULT_MODEL,
    UNKNOWN_MODEL,
    ModelUsage,
    TaskCost,
    Usage,
    aggregate_report,
    compute_cost,
    merge_model_usage,
    parse_model_usage,
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

    def test_model_override(self) -> None:
        usage = Usage(
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
    cost_source: str = "sdk",
    trace_cost_usd: float | None = None,
    models: tuple[str, ...] | None = None,
) -> TaskCost:
    """Build a TaskCost record for aggregate_report tests."""
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
        cost_usd=cost_usd,
        cost_source=cost_source,
        trace_cost_usd=cost_usd if trace_cost_usd is None else trace_cost_usd,
        models=models if models is not None else (model,),
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
        assert report["unpriced_models"] == []

    def test_unpriced_model_is_surfaced_when_trace_derived(self) -> None:
        report = aggregate_report(
            [
                _make_cost(
                    task_id="t1", model="claude-sonnet-4-6", cost_source="trace"
                ),
                _make_cost(task_id="t2", model="claude-opus-4-8", cost_source="trace"),
            ]
        )
        assert report["unpriced_models"] == ["claude-opus-4-8"]

    def test_unpriced_models_deduped_and_sorted(self) -> None:
        report = aggregate_report(
            [
                _make_cost(task_id="t1", model="claude-opus-4-8", cost_source="trace"),
                _make_cost(task_id="t2", model="claude-fable-5", cost_source="trace"),
                _make_cost(task_id="t3", model="claude-opus-4-8", cost_source="trace"),
            ]
        )
        assert report["unpriced_models"] == ["claude-fable-5", "claude-opus-4-8"]

    def test_vendor_priced_model_is_not_flagged_unpriced(self) -> None:
        """The vendor prices opus-4-8 itself — PRICING never touches it."""
        report = aggregate_report(
            [
                _make_cost(task_id="t1", model="claude-opus-4-8", cost_source="sdk"),
                _make_cost(task_id="t2", model="claude-fable-5", cost_source="sdk"),
            ]
        )
        assert report["unpriced_models"] == []

    def test_unpriced_secondary_model_is_surfaced(self) -> None:
        """A trace-derived run's flagged models come from `models`, not just the primary."""
        report = aggregate_report(
            [
                _make_cost(
                    task_id="t1",
                    model="claude-sonnet-4-6",
                    models=("claude-sonnet-4-6", "claude-fable-5"),
                    cost_source="trace",
                )
            ]
        )
        assert report["unpriced_models"] == ["claude-fable-5"]


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

    def test_flat_block(self, tmp_path: Path) -> None:
        p = _write_stdout(
            tmp_path / "agent_stdout.log", _vendor(_model_entry(cost=0.75))
        )
        (usage,) = parse_model_usage(p).models
        assert usage.model == UNKNOWN_MODEL
        assert usage.cost_usd == 0.75

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

    def test_empty_file(self, tmp_path: Path) -> None:
        p = _write_stdout(tmp_path / "agent_stdout.log", "")
        assert parse_model_usage(p) is None

    def test_absent_block(self, tmp_path: Path) -> None:
        p = _write_stdout(tmp_path / "agent_stdout.log", {"total_cost_usd": 1.0})
        assert parse_model_usage(p) is None

    def test_malformed_content(self, tmp_path: Path) -> None:
        p = _write_stdout(tmp_path / "agent_stdout.log", "not json at all {{{")
        assert parse_model_usage(p) is None

    def test_block_with_no_model_entries(self, tmp_path: Path) -> None:
        """A block carrying no usable entries must fall back, not bill zero."""
        p = _write_stdout(tmp_path / "agent_stdout.log", _vendor({"junk": 1}))
        assert parse_model_usage(p) is None


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
        assert report["cost_sources"]["sdk"] == 2
        assert report["cost_sources"]["trace"] == 1

    def test_trace_derived_runs_are_named(self) -> None:
        report = aggregate_report(
            [
                _make_cost(task_id="good", cost_source="sdk"),
                _make_cost(task_id="degraded", mode="baseline", cost_source="trace"),
            ]
        )
        assert report["cost_sources"]["trace_derived_task_ids"] == ["degraded:baseline"]

    def test_reconciliation_delta_is_published(self) -> None:
        # Vendor says $10; the old trace derivation would have said $4.
        report = aggregate_report(
            [_make_cost(task_id="t1", cost_usd=10.0, trace_cost_usd=4.0)]
        )
        rec = report["cost_sources"]["reconciliation"]
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
        rec = report["cost_sources"]["reconciliation"]
        assert rec["vendor_cost_usd"] == 10.0
        assert rec["trace_derived_cost_usd"] == 4.0

    def test_empty_costs_reconcile_without_dividing_by_zero(self) -> None:
        rec = aggregate_report([])["cost_sources"]["reconciliation"]
        assert rec["trace_over_vendor_ratio"] == 0.0


class TestConsumerContract:
    """generate_report.py and generate_charts.py read these keys. Do not drop them."""

    def test_keys_generate_report_reads(self) -> None:
        report = aggregate_report([_make_cost(mode="hybrid", cost_usd=2.0)])
        assert "total_cost_usd" in report
        assert "total_tasks" in report
        stats = report["by_mode"]["hybrid"]
        assert {"count", "total_cost", "avg_cost"} <= stats.keys()

    def test_keys_generate_charts_reads(self) -> None:
        report = aggregate_report([_make_cost(task_id="t1", mode="hybrid")])
        entry = report["per_task"][0]
        assert "task_id" in entry
        assert "cost_usd" in entry
        assert "total_cost" in report["by_mode"]["hybrid"]


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

    def test_model_entry_missing_cost_is_rejected(self, tmp_path: Path) -> None:
        """Summing only the entries that parsed would bill $2.00 of a $3.00 run."""
        p = _write_stdout(
            tmp_path / "agent_stdout.log",
            _vendor(
                {
                    "claude-sonnet-4-6": _model_entry(cost=2.0),
                    "claude-haiku-4-5": {"inputTokens": 1000},  # no costUSD
                },
                total=3.0,
            ),
        )
        assert parse_model_usage(p) is None

    def test_non_dict_model_entry_is_rejected(self, tmp_path: Path) -> None:
        """A truncated entry must not be silently skipped."""
        p = _write_stdout(
            tmp_path / "agent_stdout.log",
            _vendor(
                {
                    "claude-sonnet-4-6": _model_entry(cost=2.0),
                    "claude-haiku-4-5": "truncated",
                },
                total=2.0,
            ),
        )
        assert parse_model_usage(p) is None

    def test_block_failing_the_total_cost_checksum_is_rejected(
        self, tmp_path: Path
    ) -> None:
        """Every entry parses, but they do not add up to the vendor's own total."""
        p = _write_stdout(
            tmp_path / "agent_stdout.log",
            _vendor({"claude-sonnet-4-6": _model_entry(cost=2.0)}, total=3.0),
        )
        assert parse_model_usage(p) is None

    def test_block_with_no_total_is_rejected(self, tmp_path: Path) -> None:
        """No total means no checksum. An unverifiable block is not authoritative.

        Accepting it would bill the sum of whatever happened to parse with nothing
        able to detect a missing model — the exact silent undercount this gate
        exists to stop, arrived at by skipping the gate rather than failing it.
        """
        p = _write_stdout(
            tmp_path / "agent_stdout.log",
            _vendor({"claude-sonnet-4-6": _model_entry(cost=2.0)}, total=_OMIT),
        )
        assert parse_model_usage(p) is None

    def test_block_with_non_numeric_total_is_rejected(self, tmp_path: Path) -> None:
        p = _write_stdout(
            tmp_path / "agent_stdout.log",
            _vendor({"claude-sonnet-4-6": _model_entry(cost=2.0)}, total="2.0"),
        )
        assert parse_model_usage(p) is None

    def test_nan_cost_is_rejected(self, tmp_path: Path) -> None:
        """json.loads accepts bare NaN, and every comparison against NaN is False.

        A NaN cost would sail through the checksum untouched (abs(nan - x) > tol is
        False) and then poison the batch total: one unreadable run turning the whole
        report's bottom line into NaN, and cost_report.json into invalid JSON.
        """
        p = _write_stdout(
            tmp_path / "agent_stdout.log",
            '{"modelUsage": {"claude-sonnet-4-6": {"costUSD": NaN}}, '
            '"total_cost_usd": 2.0}',
        )
        assert parse_model_usage(p) is None

    def test_nan_total_is_rejected(self, tmp_path: Path) -> None:
        p = _write_stdout(
            tmp_path / "agent_stdout.log",
            '{"modelUsage": {"claude-sonnet-4-6": {"costUSD": 2.0}}, '
            '"total_cost_usd": NaN}',
        )
        assert parse_model_usage(p) is None

    def test_bool_cost_is_rejected(self, tmp_path: Path) -> None:
        """bool is a subclass of int, so `True` would otherwise bill as $1.00."""
        p = _write_stdout(
            tmp_path / "agent_stdout.log",
            _vendor({"claude-sonnet-4-6": {"costUSD": True}}, total=1.0),
        )
        assert parse_model_usage(p) is None

    def test_null_cost_is_rejected(self, tmp_path: Path) -> None:
        """An explicit null must not fold into a silent $0.00 for that model."""
        p = _write_stdout(
            tmp_path / "agent_stdout.log",
            _vendor({"claude-sonnet-4-6": {"costUSD": None}}, total=2.0),
        )
        assert parse_model_usage(p) is None

    def test_infinite_token_count_is_rejected_not_crashed(self, tmp_path: Path) -> None:
        """int(float("inf")) raises OverflowError, which is not a ValueError.

        Uncaught, it would escape the per-run fallback and abort the entire batch
        scan over one malformed log.
        """
        p = _write_stdout(
            tmp_path / "agent_stdout.log",
            '{"modelUsage": {"claude-sonnet-4-6": '
            '{"inputTokens": Infinity, "costUSD": 2.0}}, "total_cost_usd": 2.0}',
        )
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

    def test_mixed_flat_and_per_model_shape_is_rejected(self, tmp_path: Path) -> None:
        """Ambiguous shape: classifying it as flat would bill $0."""
        p = _write_stdout(
            tmp_path / "agent_stdout.log",
            _vendor(
                {
                    "inputTokens": 100,
                    "claude-sonnet-4-6": _model_entry(cost=2.0),
                },
                total=2.0,
            ),
        )
        assert parse_model_usage(p) is None

    def test_flat_block_without_cost_is_rejected(self, tmp_path: Path) -> None:
        p = _write_stdout(
            tmp_path / "agent_stdout.log",
            _vendor({"inputTokens": 100}, total=1.25),
        )
        assert parse_model_usage(p) is None

    def test_stream_partial_block_after_complete_one_is_rejected(
        self, tmp_path: Path
    ) -> None:
        """Last-wins picks the trailing partial block; the checksum catches it."""
        lines = [
            json.dumps(
                _vendor({"claude-sonnet-4-6": _model_entry(cost=3.0)}, total=3.0)
            ),
            json.dumps(
                _vendor({"claude-sonnet-4-6": _model_entry(cost=0.5)}, total=3.0)
            ),
        ]
        p = _write_stdout(tmp_path / "agent_stdout.log", "\n".join(lines))
        assert parse_model_usage(p) is None

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
        assert report["cost_sources"]["trace"] == 1
        assert report["cost_sources"]["trace_derived_task_ids"] == ["my-task:hybrid"]

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

        assert math.isfinite(report["total_cost_usd"])
        assert report["total_cost_usd"] > 0
        # The corrupt run is disclosed as trace-derived, not billed at NaN.
        assert report["cost_sources"]["trace_derived_task_ids"] == ["bad-task:hybrid"]
        # And the report is still strictly valid JSON (allow_nan would emit `NaN`).
        json.loads(json.dumps(report, allow_nan=False))
