"""Tests for eb_metrics.usage_dedup — the one request-level token dedup.

Cost accounting (``scripts/cost_tracker.py``) and the retrieval axis
(``eb_metrics.retrieval_extraction``) used to carry a dedup each, and they
disagreed: cost billed a request at its max-output snapshot, the axis at its
first (EnterpriseBench-4p5k, review C4). They now drive this one accumulator.
The cross-consumer test at the bottom is the guard that keeps them from drifting
apart again.
"""

from __future__ import annotations

import json
import logging
import sys
from pathlib import Path

import pytest

from eb_metrics.retrieval_extraction import compute_ttfr
from eb_metrics.usage_dedup import UsageDedup, request_key

# Make scripts importable (cost_tracker is the other consumer).
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))

from cost_tracker import parse_trace  # noqa: E402


def _entry(request_id: str | None = None, msg_id: str | None = None, **usage) -> dict:
    message: dict = {"usage": usage}
    if msg_id is not None:
        message["id"] = msg_id
    entry: dict = {"type": "assistant", "message": message}
    if request_id is not None:
        entry["requestId"] = request_id
    return entry


def _offer_all(entries: list[dict]) -> UsageDedup:
    dedup = UsageDedup()
    for entry in entries:
        dedup.offer(entry, entry["message"].get("usage"))
    return dedup


# ---------------------------------------------------------------------------
# request_key
# ---------------------------------------------------------------------------


def test_request_key_prefers_request_id_then_message_id() -> None:
    assert request_key(_entry(request_id="req_1", msg_id="msg_1")) == "req_1"
    assert request_key(_entry(msg_id="msg_1")) == "msg_1"
    assert request_key(_entry()) is None


def test_request_key_ignores_empty_and_non_string_ids() -> None:
    assert request_key({"requestId": ""}) is None
    assert request_key({"requestId": 17}) is None


# ---------------------------------------------------------------------------
# Per-request selection: max-output wins
# ---------------------------------------------------------------------------


def test_request_billed_at_its_complete_output_not_its_first() -> None:
    # output_tokens streams upward across a request's content-block lines; the
    # last one carries the whole request. First-wins would bill 140.
    dedup = _offer_all(
        [
            _entry(request_id="r", input_tokens=1000, output_tokens=140),
            _entry(request_id="r", input_tokens=1000, output_tokens=900),
            _entry(request_id="r", input_tokens=1000, output_tokens=2589),
        ]
    )
    assert dedup.num_requests == 1
    assert dedup.field_total("input_tokens") == 1000  # invariant, billed once
    assert dedup.field_total("output_tokens") == 2589
    assert dedup.total_tokens == 3589


def test_junk_and_bool_field_values_are_billed_as_absent() -> None:
    # JSON is untrusted: a non-numeric field must not crash the walk, and a bool
    # must not sneak through as int(True) == 1.
    dedup = _offer_all(
        [_entry(request_id="r", input_tokens="many", cache_read_input_tokens=True,
                output_tokens=3)]
    )
    assert dedup.field_total("input_tokens") == 0
    assert dedup.field_total("cache_read_input_tokens") == 0
    assert dedup.total_tokens == 3


@pytest.mark.parametrize("junk", [float("inf"), float("-inf"), float("nan")])
def test_non_finite_field_values_are_billed_as_absent(junk: float) -> None:
    # json.loads accepts the non-standard Infinity / NaN literals, and neither
    # survives int(): int(inf) raises OverflowError, int(nan) ValueError. Both
    # consumers walk this, so an unreadable field must not crash the trace.
    dedup = _offer_all([_entry(request_id="r", input_tokens=junk, output_tokens=3)])
    assert dedup.field_total("input_tokens") == 0
    assert dedup.total_tokens == 3


def test_an_int_too_large_for_a_float_is_billed_exactly() -> None:
    # json.loads accepts an integer literal of up to ~4300 digits
    # (sys.int_info.default_max_str_digits), so it can hand this boundary an int
    # that float() cannot hold — float overflows around 309 digits, an order of
    # magnitude sooner. Such a value is an exact token count, not junk: bill it,
    # and do not route it through math.isfinite on the way (that raises
    # OverflowError on an int this size, which is the crash the guard prevents).
    huge = int("9" * 400)
    dedup = _offer_all([_entry(request_id="r", input_tokens=huge, output_tokens=3)])
    assert dedup.field_total("input_tokens") == huge
    assert dedup.total_tokens == huge + 3


def test_a_requests_other_lines_still_bill_a_field_one_line_reported_as_junk() -> None:
    # Counting junk as absent rather than raising is only safe because the max
    # lets a sibling line supply the real number.
    dedup = _offer_all(
        [
            _entry(request_id="r", input_tokens=float("nan"), output_tokens=140),
            _entry(request_id="r", input_tokens=1000, output_tokens=2589),
        ]
    )
    assert dedup.field_total("input_tokens") == 1000
    assert dedup.total_tokens == 3589


@pytest.mark.parametrize("junk", ["1000", float("inf"), float("nan"), True])
def test_junk_field_value_is_reported_not_silently_zeroed(
    junk: object, caplog: pytest.LogCaptureFixture
) -> None:
    # Counting junk as absent can undercount, so it must never be quiet: a $0.00
    # line in a cost report with nothing to explain it is the failure mode.
    with caplog.at_level(logging.WARNING, logger="eb_metrics.usage_dedup"):
        _offer_all([_entry(request_id="r", input_tokens=junk)])
    assert "input_tokens" in caplog.text
    assert repr(junk) in caplog.text


def test_an_absent_field_is_not_reported_as_junk(
    caplog: pytest.LogCaptureFixture,
) -> None:
    # Omitting a field is ordinary — only unreadable values are worth a warning,
    # or the signal drowns in noise on every well-formed trace. An explicit null
    # reports no count either, so it is absent too, not junk.
    with caplog.at_level(logging.WARNING, logger="eb_metrics.usage_dedup"):
        _offer_all(
            [
                _entry(request_id="r", output_tokens=3),
                _entry(request_id="s", input_tokens=None),
            ]
        )
    assert caplog.text == ""


def test_trailing_zero_error_record_does_not_erase_a_request() -> None:
    # A trailing all-zero isApiErrorMessage record is why this is max-output and
    # not plain last-wins: last-wins would bill the request at zero.
    dedup = _offer_all(
        [
            _entry(request_id="r", input_tokens=1000, output_tokens=2589),
            _entry(request_id="r", input_tokens=0, output_tokens=0),
        ]
    )
    assert dedup.total_tokens == 3589


def test_distinct_requests_are_summed() -> None:
    dedup = _offer_all(
        [
            _entry(request_id="a", output_tokens=100),
            _entry(request_id="b", output_tokens=50),
        ]
    )
    assert dedup.num_requests == 2
    assert dedup.total_tokens == 150


def test_group_lines_need_not_be_contiguous() -> None:
    dedup = _offer_all(
        [
            _entry(request_id="a", output_tokens=100),
            _entry(request_id="b", output_tokens=50),
            _entry(request_id="a", output_tokens=300),
        ]
    )
    assert dedup.num_requests == 2
    assert dedup.total_tokens == 350  # 300 (a, upgraded) + 50 (b)


def test_keyless_lines_are_each_their_own_request() -> None:
    # Legacy single-block traces carry no requestId/message.id; each line is a
    # request in its own right and must be billed once, not collapsed together.
    dedup = _offer_all(
        [_entry(output_tokens=10), _entry(output_tokens=20)]
    )
    assert dedup.num_requests == 2
    assert dedup.total_tokens == 30
    assert dedup.ungrouped_lines == 2


def test_lines_without_usage_leave_any_usage_false() -> None:
    # A usage block carrying none of the four fields is no telemetry at all — it
    # must not masquerade as "zero tokens used".
    dedup = _offer_all([_entry(request_id="r"), _entry(request_id="s", unrelated=5)])
    assert dedup.any_usage is False
    assert dedup.total_tokens == 0
    assert dedup.num_requests == 0


def test_any_usage_is_true_for_a_measured_zero() -> None:
    dedup = _offer_all([_entry(request_id="r", output_tokens=0)])
    assert dedup.any_usage is True


# ---------------------------------------------------------------------------
# Cross-consumer agreement — the reconcile invariant (EnterpriseBench-4p5k)
# ---------------------------------------------------------------------------


def test_cost_and_axis_bill_the_same_trace_identically(tmp_path: Path) -> None:
    """The two consumers must agree on what a trace's tokens cost.

    Built so the axis's cumulative spans the whole trace: the required file is
    opened on the *last* assistant line, so ``tokens_to_first_relevant`` is the
    total of every request — directly comparable to what ``parse_trace`` bills.

    This is the test that fails if someone changes one dedup and not the other.
    """
    target = "/workspace/kubernetes/pkg/kubelet/prober/worker.go"
    lines = [
        # Request 1: three content-block lines, output streaming upward.
        {
            "type": "assistant",
            "requestId": "req_1",
            "message": {
                "content": [{"type": "text", "text": "thinking"}],
                "usage": {"input_tokens": 500, "cache_read_input_tokens": 20, "output_tokens": 10},
            },
        },
        {
            "type": "assistant",
            "requestId": "req_1",
            "message": {
                "content": [{"type": "text", "text": "more"}],
                "usage": {"input_tokens": 500, "cache_read_input_tokens": 20, "output_tokens": 700},
            },
        },
        # Request 2: closes on the tool_use that opens the required file.
        {
            "type": "assistant",
            "requestId": "req_2",
            "timestamp": "2026-01-01T00:00:05.000Z",
            "message": {
                "content": [
                    {"type": "tool_use", "id": "t1", "name": "Read", "input": {"file_path": target}}
                ],
                "usage": {"input_tokens": 300, "cache_creation_input_tokens": 40, "output_tokens": 900},
            },
        },
    ]
    trace = tmp_path / "agent_trace.jsonl"
    trace.write_text("\n".join(json.dumps(line) for line in lines) + "\n")

    cost = parse_trace(trace)
    cost_total = (
        cost.input_tokens
        + cost.output_tokens
        + cost.cache_write_tokens
        + cost.cache_read_tokens
    )
    # req_1 billed at output 700: 500 + 20 + 700 = 1220
    # req_2:                       300 + 40 + 900 = 1240
    assert cost_total == 2460
    assert cost.num_requests == 2

    axis = compute_ttfr(trace, ["pkg/kubelet/prober/worker.go"])
    assert axis.coverage == "ok"
    assert axis.tokens_to_first_relevant == cost_total
