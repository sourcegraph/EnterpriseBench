"""One request-level dedup for agent-trace token usage.

An ``agent_trace.jsonl`` emits one assistant line per *content block* (thinking /
text / tool_use), and every line of one API request repeats that request's usage
snapshot. Counting each line therefore bills a multi-block turn once per block.

Both token consumers drive this one accumulator — cost accounting
(``scripts/cost_tracker.py``, which needs the final per-field breakdown) and the
retrieval axis (``eb_metrics.retrieval_extraction``, which needs each tool_use
step's running cost for TTFR). The two decisions that must never drift between
them — how lines group into requests (:func:`request_key`), and which snapshot a
request bills (:meth:`UsageDedup.offer`) — live here.

Extracting the *usage* off a line does not: the axis also reads flat entries
whose ``usage`` sits at top level, while cost reads only nested
``message.usage``, so each caller still passes in the usage it found. The
consumers can therefore still disagree about which lines carry usage at all —
only the grouping and selection are shared.
"""

from __future__ import annotations

import logging
import math
from typing import Any, Mapping

logger = logging.getLogger(__name__)

# The four Anthropic usage-token fields, in the order they are reported.
USAGE_FIELDS = (
    "input_tokens",
    "cache_creation_input_tokens",
    "cache_read_input_tokens",
    "output_tokens",
)


def request_key(entry: Mapping[str, Any]) -> str | None:
    """The API request a trace line belongs to, or ``None`` if it announces none.

    ``requestId`` carries the grouping in every assistant line of the current
    corpus; ``message.id`` is a verified 1:1 stand-in for a writer that omits it.
    """
    rid = entry.get("requestId")
    if isinstance(rid, str) and rid:
        return rid

    message = entry.get("message")
    msg_id = message.get("id") if isinstance(message, Mapping) else None
    return msg_id if isinstance(msg_id, str) and msg_id else None


def _field_value(usage: Mapping[str, Any], field: str) -> int:
    """One usage field as a token count. Absent counts zero; junk counts zero loudly.

    A trace is third-party input: ``json.loads`` accepts the non-standard literals
    ``Infinity`` and ``NaN``, and nothing stops a writer emitting a count as a
    string. Counting an unreadable field as absent rather than raising keeps it
    from crashing both consumers mid-trace, and lets the request's other lines
    still bill the field through :meth:`UsageDedup.offer`'s max. It can also
    undercount, hence the warning — a silent zero surfaces as a $0.00 line in a
    cost report explaining nothing.
    """
    value = usage.get(field)
    if value is None:
        # Absent, or an explicit null: the line reports no count for this field.
        return 0
    if isinstance(value, int) and not isinstance(value, bool):
        # Must precede math.isfinite, which raises OverflowError on an int too
        # large for a float — reachable well below the ~4300-digit literal
        # CPython's parser accepts (sys.int_info.default_max_str_digits).
        return value
    if isinstance(value, float) and math.isfinite(value):
        return int(value)
    logger.warning(
        "usage field %s is %r, not a token count; counting it as absent",
        field,
        value,
    )
    return 0


def _has_usage(usage: Any) -> bool:
    """Whether a line carries usage telemetry at all.

    Presence of a *field*, not a non-zero value: a request that measured zero
    output tokens still carries usage, while a trace with no telemetry at all
    must not masquerade as "zero tokens used".
    """
    return isinstance(usage, Mapping) and any(f in usage for f in USAGE_FIELDS)


class UsageDedup:
    """Accumulates trace usage, keeping each request's max-output snapshot.

    Feed every assistant line to :meth:`offer` in trace order, then read the
    totals. Both consumers see the same per-request selection.
    """

    def __init__(self) -> None:
        # Keyed by request; insertion order is first-appearance order, which is
        # what :meth:`billed_prefix` walks.
        self._selected: dict[str | int, dict[str, int]] = {}
        self.any_usage = False
        self.ungrouped_lines = 0

    def offer(self, entry: Mapping[str, Any], usage: Any) -> None:
        """Account one assistant line against its request.

        Within a request the usage snapshot is not constant: ``input_tokens`` and
        the cache counts are invariant, but ``output_tokens`` streams upward and
        is complete only on the request's final line — so its *first* line
        undercounts output (140 against a true 2589 on real EB traces).

        A request therefore bills the per-field *max* over its lines. Unlike
        last-wins, that cannot be walked backwards by a line reporting a smaller
        value or omitting a field outright — a trailing all-zero
        ``isApiErrorMessage`` record, or a later line carrying only
        ``output_tokens``, would otherwise zero what was already billed.
        """
        if not _has_usage(usage):
            return
        self.any_usage = True

        key: str | int | None = request_key(entry)
        if key is None:
            # A line announcing no request of its own is its own request, billed
            # exactly once — correct for legacy single-block traces. A negative
            # int can never collide with a requestId, so counting them is enough
            # to key them apart; the caller does not have to supply a line number.
            self.ungrouped_lines += 1
            key = -self.ungrouped_lines

        billed = self._selected.get(key)
        if billed is None:
            billed = dict.fromkeys(USAGE_FIELDS, 0)
            self._selected[key] = billed
        for field in USAGE_FIELDS:
            billed[field] = max(billed[field], _field_value(usage, field))

    def field_total(self, field: str) -> int:
        """The billed total for one usage field across all requests."""
        return sum(billed[field] for billed in self._selected.values())

    @property
    def total_tokens(self) -> int:
        """The billed four-field total across all requests."""
        return sum(sum(billed.values()) for billed in self._selected.values())

    @property
    def num_requests(self) -> int:
        """How many distinct API requests carried usage."""
        return len(self._selected)

    def billed_prefix(self) -> list[int]:
        """Cumulative billed tokens after the first *n* distinct requests.

        ``billed_prefix()[n]`` is what the first ``n`` requests cost in total, so
        a consumer that recorded "``n`` requests had been seen when this step
        fired" can price that step once every request's final snapshot is known.
        Index 0 is 0; the last entry equals :attr:`total_tokens`. Non-decreasing,
        because a request's billed total is never negative.
        """
        sums = [0]
        for billed in self._selected.values():
            sums.append(sums[-1] + sum(billed.values()))
        return sums
