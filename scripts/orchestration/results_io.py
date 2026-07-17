"""Single source of truth for the ``results.json`` contract every runner writes.

``run_benchmark`` dispatches a task to one of three runners by ``session_type``
(``run_task``, ``chain_runner``, ``event_replay``) and then reads back exactly
one file: ``results.json``. Its readers each reach for a different field, and no
runner sees the whole set:

  * ``run_benchmark.run_task``           — ``scores.task_score``   (the score)
  * ``run_benchmark.is_task_completed``  — ``success``             (skip-completed)
  * ``run_benchmark.extract_task_cost``  — ``tool_usage.cost_usd`` (cost rollup)
  * ``run_sweep._check_results_file``    — ``success``             (scored/failed)
  * ``scripts/analysis/recompute_headline_*``  — ``phase`` + ``success``
  * ``results/analysis/aggregate_mcp_clean.load_mode`` — ``tool_usage.num_turns``,
    THEN ``status``

The last one reads two fields in that order, and the order is the contract: it
classes ``num_turns in (0, None)`` as NO-OP *before* it looks at ``status``, so a
runner reporting ``tool_usage={}`` (chain_runner and event_replay both do — they
spend no tokens) is classed NO-OP and its ``status`` is never read. Both NO-OP
and INVALID sit outside the clean set, so nothing is miscounted today; the point
is that ``status`` alone does not carry a run out of that set. This map is only
worth having if it is exact — the readers are listed here, but each one is bound
to this module by execution in tests/test_results_io.py, not by this prose.

Declaring the shape once here is what stops a runner from satisfying the reader
it happened to be tested against while silently starving the rest — an absent key
is indistinguishable from a real default at every one of those read sites, so it
surfaces as a zero in a cost report rather than as a failure (EnterpriseBench-78zt5).
This mirrors ``runner_cli`` in the opposite direction: that module is the
authority on what run_benchmark SENDS a runner, this one on what a runner SENDS
BACK. ``write_results_json``'s signature is the *shape* authority (which keys
exist, and what an absent value means); each runner stays the *value* authority
(what its own run actually did).

What this module does NOT own: where the file lands. Runners disagree on that
(``results/runs/<task_id>/<mode>/`` vs the task directory) and reconciling it is
EnterpriseBench-5xl64. This module fixes the SHAPE; that bead fixes the LOCATION.

It does not own what ``success`` MEANS, either, and the runners are not quite
agreed: ``run_task`` reads it as "reached a terminal state without an infra
failure", so a dry run is ``success=True`` having scored nothing, while
``chain_runner`` reads it as "produced a score" and reports a clean chain with no
milestones as ``success=False``. Both are defensible for their own runner and
neither reaches a headline (a dry run is filtered long before), but a reader
comparing ``success`` ACROSS session_types should know it is not one predicate.
"""

from __future__ import annotations

import json
from pathlib import Path

# The file every runner writes and every reader above opens.
RESULTS_FILENAME = "results.json"

# Value of the ``status`` field for a run that must not be scored (e.g. an MCP
# pre-flight failure, a broken grading-asset seal). Whether such a run is
# re-runnable is decided by the ``failure_class`` field, not by this marker:
# verifier_infra_error earns a fresh attempt, integrity_violation never does.
#
# Lives here rather than in run_task because it is part of the written shape, and
# chain_runner needs to say it without importing the whole single-task runner.
RUN_STATUS_INVALID = "invalid"

# Value of the ``phase`` field for a run that got all the way through. As
# load-bearing as RUN_STATUS_INVALID and declared here for the same reason: the
# headline aggregators admit a row only on ``phase == "complete" and success is
# True`` (scripts/analysis/recompute_headline_*.py), and run_task derives
# INVALID from it (``_effective_status``), so a runner that typo'd it would drop
# its task out of the headline rather than fail. Readers compare against the
# literal, so this constant's VALUE is part of the contract, not just its name.
PHASE_COMPLETE = "complete"

# Top-level keys of results.json, in write order. Every payload carries all of
# them: a reader must never have to distinguish "this runner omitted the key"
# from "this run had no value for it".
RESULTS_FIELDS: tuple[str, ...] = (
    "task_id",
    "success",
    "phase",
    "status",
    "error",
    "failure_class",
    "image_tag",
    "scores",
    "timing",
    "tool_usage",
    "config",
    "task_metadata",
)


def write_results_json(
    output_dir: Path,
    *,
    task_id: str,
    success: bool,
    phase: str = "",
    status: str = "",
    error: str = "",
    failure_class: str | None = None,
    image_tag: str = "",
    scores: dict | None = None,
    timing: dict | None = None,
    tool_usage: dict | None = None,
    config: dict | None = None,
    task_metadata: dict | None = None,
) -> Path:
    """Write ``results.json`` into *output_dir* and return the path.

    This signature is the one place the shape is decided. ``task_id`` and
    ``success`` are required because every known reader needs one or the other
    and neither has a defensible default. The rest describe the run and default
    to empty, which is what a runner that does not have the concept
    (``event_replay`` has no image tag, offline scoring has no tool usage) should
    report: present and empty, not missing. Keyword-only, so a misspelled field
    raises TypeError at the call site rather than writing a key no reader reads.
    """
    payload = {
        "task_id": task_id,
        "success": success,
        "phase": phase,
        "status": status,
        "error": error,
        "failure_class": failure_class,
        "image_tag": image_tag,
        "scores": {} if scores is None else scores,
        "timing": {} if timing is None else timing,
        "tool_usage": {} if tool_usage is None else tool_usage,
        "config": {} if config is None else config,
        "task_metadata": {} if task_metadata is None else task_metadata,
    }
    output_dir.mkdir(parents=True, exist_ok=True)
    results_path = output_dir / RESULTS_FILENAME
    results_path.write_text(json.dumps(payload, indent=2) + "\n")
    return results_path
