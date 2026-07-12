"""One request-level dedup for agent-trace token usage.

An ``agent_trace.jsonl`` emits one assistant line per *content block* (thinking /
text / tool_use), and every line of one API request repeats that request's usage
snapshot. Counting each line therefore bills a multi-block turn once per block.

Within a request the snapshot is not constant: ``input_tokens`` and the cache
counts are invariant, but ``output_tokens`` streams upward and is complete only
on the request's final line. The request's *first* line therefore undercounts
output (140 against a true 2589 on real EB traces). A request accordingly bills
the per-field max over its lines — see :meth:`UsageDedup.offer` for why the max
per field, and not whichever single line looked most complete.

Both token consumers drive this one accumulator — cost accounting
(``scripts/cost_tracker.py``, which needs the final per-field breakdown) and the
retrieval axis (``eb_metrics.retrieval_extraction``, which needs each tool_use
step's running cost for TTFR). Two dedups could drift on what a request cost;
one cannot.

A request's billed total is only known once its *last* line has been read, so
nothing here is resolved mid-stream: both consumers read their totals after the
walk. The axis resolves a step's tokens retrospectively via
:meth:`UsageDedup.billed_prefix` — on real traces 30% of tool_use blocks sit on
a non-final line of their request, so a step that froze the snapshot it saw
would report a partial output for those.

Trace-shape handling deliberately stays with each caller (the axis also reads
flat entries whose ``usage`` sits at top level; cost reads only nested
``message.usage``). What must never drift between them — how lines group into
requests, and which snapshot a request bills — lives here.
"""

from __future__ import annotations

from typing import Any, Mapping

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
    """One usage field as an int, treating junk (and ``bool``) as absent."""
    value = usage.get(field, 0)
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return 0
    return int(value)


def usage_total(usage: Any) -> int | None:
    """Sum the four usage-token fields on one message.

    ``None`` (not ``0``) when ``usage`` carries none of them, so a trace with no
    usage telemetry at all cannot masquerade as "zero tokens used".
    """
    if not isinstance(usage, Mapping) or not any(f in usage for f in USAGE_FIELDS):
        return None
    return sum(_field_value(usage, f) for f in USAGE_FIELDS)


class UsageDedup:
    """Accumulates trace usage, keeping each request's max-output snapshot.

    Feed every assistant line to :meth:`offer` in trace order, then read the
    totals. Both consumers see the same per-request selection.
    """

    def __init__(self) -> None:
        # A line with no request key of its own is keyed by line number (an int,
        # so it can never collide with a requestId) and billed exactly once —
        # correct for legacy single-block traces, where each line is its own
        # request. Insertion order is first-appearance order, which is what
        # :meth:`billed_prefix` walks.
        self._selected: dict[str | int, dict[str, int]] = {}
        self.any_usage = False
        self.ungrouped_lines = 0

    def offer(self, entry: Mapping[str, Any], usage: Any, line_no: int) -> None:
        """Account one assistant line against its request.

        A request bills the per-field *max* over its lines, rather than whichever
        single line looked most complete. Both reduce to the same thing on a
        well-formed request — input and cache counts are invariant across its
        lines, and ``output_tokens`` only grows — but taking the max per field
        needs no such assumption to hold: it cannot be walked backwards by a line
        that reports a smaller value or omits a field outright (a later line
        carrying only ``output_tokens`` would otherwise zero the input and cache
        tokens already billed), and it does not care what order the lines arrive
        in. A trailing all-zero ``isApiErrorMessage`` record is absorbed for
        free, since zero never wins a max.
        """
        if usage_total(usage) is None:
            return
        self.any_usage = True

        found = request_key(entry)
        if found is None:
            self.ungrouped_lines += 1
        key: str | int = found if found is not None else line_no

        billed = self._selected.setdefault(key, dict.fromkeys(USAGE_FIELDS, 0))
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
