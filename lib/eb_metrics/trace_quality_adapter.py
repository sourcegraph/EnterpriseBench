"""EnterpriseBench → codeprobe TraceQualityMetrics adapter.

Projects one EnterpriseBench ``results/<run>/results.json`` record onto a
codeprobe :class:`~codeprobe.analysis.trace_quality.TraceQualityMetrics`
row so EB runs can surface in any tooling consuming the shared
``quality_metrics`` schema (codeprobe ``aggregate.json`` format,
schema_version 1).

ZFC compliance
--------------

Every signal emitted here is a structural read from a typed field that
the EB runner already records:

* ``phase`` and ``success`` decide validity.
* ``failure_class`` becomes ``validity_reason`` and a per-class flag.
* ``scores.task_score`` / ``scores.all_passed`` / ``scores.checkpoints_*``
  populate the score and precision fields.
* Per-checkpoint failures fan out to ``checkpoint_<name>_failed`` flags
  so the existing EB dashboards keep their checkpoint-level signal.

There is no keyword matching, model call, or hardcoded threshold for
semantic properties. Adding a new failure_class or phase value is
non-breaking — it shows up as a new flag without code changes.

The only semantic assumption is the validity rule itself, which is
fixed by the schema: a trial is *valid* exactly when ``phase ==
"complete"`` AND ``success`` is true. Any other state is *invalid* and
its ``validity_reason`` is whatever EB already classified it as.
"""

from __future__ import annotations

import json
from collections.abc import Iterable, Iterator
from pathlib import Path
from typing import Any, Mapping

from codeprobe.analysis.trace_quality import (
    LOW_RECALL_THRESHOLD,
    TraceQualityMetrics,
)

from eb_metrics.ir_metrics import IRScores
from eb_metrics.retrieval_extraction import compute_run_ir_scores, resolve_ground_truth

__all__ = [
    "metrics_from_eb_record",
    "metrics_from_results_file",
]


def _retrieval_f1(scores: IRScores) -> float:
    """Global retrieval F1 = harmonic mean of context-efficiency (precision
    over all retrieved) and file-level recall. Pairs with the scalar
    ``recall`` (=``file_recall``) surfaced on the row; the full per-k IR
    object is carried in ``detail['retrieval_metrics']``.
    """
    p = scores.context_efficiency
    r = scores.file_recall
    if p + r == 0:
        return 0.0
    return round(2 * p * r / (p + r), 4)


def _coerce_float(value: Any) -> float | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        return float(value)
    return None


def _coerce_bool(value: Any) -> bool | None:
    if isinstance(value, bool):
        return value
    return None


def _checkpoint_flags(checkpoints: Iterable[Mapping[str, Any]]) -> list[str]:
    """Emit ``checkpoint_<name>_failed`` for every failing checkpoint.

    Pure structural projection — a checkpoint with ``passed == False``
    becomes a flag whose name is bound to the checkpoint name. The same
    flag never appears twice for one trial; ordering is preserved by
    insertion so deterministic flag tuples are easy to assert against.
    """
    seen: list[str] = []
    for cp in checkpoints:
        if not isinstance(cp, Mapping):
            continue
        name = cp.get("name")
        if not isinstance(name, str) or not name:
            continue
        passed = cp.get("passed")
        if passed is False:
            flag = f"checkpoint_{name}_failed"
            if flag not in seen:
                seen.append(flag)
    return seen


def metrics_from_eb_record(
    record: Mapping[str, Any],
    *,
    run_name: str,
    repeat_index: int = 0,
    retrieval: IRScores | None = None,
) -> TraceQualityMetrics:
    """Project one EB ``results.json`` record onto a TraceQualityMetrics row.

    Mapping (all structural, ZFC-compliant):

    * ``phase == "complete"`` and ``success`` truthy →
      ``validity = "valid"``, ``validity_reason = None``.
    * Otherwise → ``validity = "invalid"``,
      ``validity_reason = failure_class or phase``.
    * ``score = scores.task_score`` when valid, else ``None``.
    * ``scorer_passed = scores.all_passed`` cast to bool when present.
    * ``precision = checkpoints_passed / checkpoints_total`` when
      ``checkpoints_total > 0`` — the *checkpoint* pass-rate, an
      outcome-axis signal distinct from retrieval precision.
    * ``recall``/``f1`` carry the *retrieval* axis when a ported
      :class:`~eb_metrics.ir_metrics.IRScores` is supplied via the
      ``retrieval`` argument: ``recall = file_recall`` (fraction of a
      task's ``required_files`` the agent opened) and ``f1`` = its harmonic
      mean with context-efficiency. Both stay ``None`` when no retrieval
      signal is available. The full per-k IR object — including
      localization@1/@k and the turns-/tokens-/seconds-to-first-relevant
      (TTFR) fields (jn73.1) — is stored under
      ``detail['retrieval_metrics']``. ``recall`` below
      ``LOW_RECALL_THRESHOLD`` adds the ``low_recall`` flag (mirroring
      codeprobe's native oracle path); a TTFR ``ttfr_coverage`` of
      ``"no_tool_calls"`` (an observability gap, not a genuine miss) adds
      ``ttfr_unavailable`` rather than leaving a silently absent signal.
    * Flags: a per-failure-class flag for every non-null
      ``failure_class``; ``invalid`` for every invalid trial; one
      ``checkpoint_<name>_failed`` flag per failing checkpoint.
    * ``detail`` carries ``error``, ``phase``, and the
      ``task_metadata.{suite, task_type}`` fields when present.
    * ``config_label = run_name`` — the per-run directory under
      ``results/`` that codeprobe treats as a config bucket in
      ``aggregate.json``.

    ``repeat_index`` is exposed because EB does not yet emit a repeat
    counter; callers can pass one when running multiple trials per task.
    The default of ``0`` matches codeprobe's single-trial convention.
    """
    task_id = record.get("task_id")
    if not isinstance(task_id, str) or not task_id:
        raise ValueError("EB result record is missing a string task_id")

    phase = record.get("phase")
    success = bool(record.get("success"))
    failure_class = record.get("failure_class")
    failure_class = failure_class if isinstance(failure_class, str) and failure_class else None

    is_valid = phase == "complete" and success
    validity = "valid" if is_valid else "invalid"

    if is_valid:
        validity_reason: str | None = None
    else:
        validity_reason = failure_class or (phase if isinstance(phase, str) and phase else None) or "unknown"

    scores = record.get("scores") if isinstance(record.get("scores"), Mapping) else {}
    score = _coerce_float(scores.get("task_score")) if is_valid else None
    scorer_passed = _coerce_bool(scores.get("all_passed"))

    cp_total = scores.get("checkpoints_total")
    cp_passed = scores.get("checkpoints_passed")
    if isinstance(cp_total, int) and cp_total > 0 and isinstance(cp_passed, int):
        precision: float | None = cp_passed / cp_total
    else:
        precision = None

    flags: list[str] = []
    if not is_valid:
        flags.append("invalid")
    if failure_class is not None:
        flags.append(failure_class)

    cps = scores.get("checkpoints")
    if isinstance(cps, list):
        flags.extend(_checkpoint_flags(cps))

    detail: dict[str, Any] = {}
    err = record.get("error")
    if isinstance(err, str) and err.strip():
        detail["error"] = err
    if isinstance(phase, str) and phase:
        detail["phase"] = phase
    task_meta = record.get("task_metadata")
    if isinstance(task_meta, Mapping):
        suite = task_meta.get("suite")
        if isinstance(suite, str) and suite:
            detail["suite"] = suite
        task_type = task_meta.get("task_type")
        if isinstance(task_type, str) and task_type:
            detail["task_type"] = task_type

    recall: float | None = None
    f1: float | None = None
    if retrieval is not None:
        recall = retrieval.file_recall
        f1 = _retrieval_f1(retrieval)
        detail["retrieval_metrics"] = retrieval.to_dict()
        if recall < LOW_RECALL_THRESHOLD:
            flags.append("low_recall")
        if retrieval.ttfr_coverage == "no_tool_calls":
            flags.append("ttfr_unavailable")

    return TraceQualityMetrics(
        task_id=task_id,
        config_label=run_name,
        repeat_index=repeat_index,
        validity=validity,
        validity_reason=validity_reason,
        score=score,
        scorer_passed=scorer_passed,
        precision=precision,
        recall=recall,
        f1=f1,
        quality_flags=tuple(dict.fromkeys(flags)),
        detail=detail,
    )


def metrics_from_results_file(
    path: Path,
    *,
    run_name: str | None = None,
    benchmarks_dir: Path | None = None,
    trace_name: str = "agent_trace.jsonl",
) -> Iterator[TraceQualityMetrics]:
    """Yield TraceQualityMetrics rows for every record in a results.json.

    EB writes ``results/<run_name>/results.json`` as a JSON array of
    per-task records. This helper streams them through
    :func:`metrics_from_eb_record` using the parent directory name as
    ``run_name`` unless one is passed explicitly.

    When ``benchmarks_dir`` is provided and the results file is a single
    record with a sibling ``<trace_name>`` (the canonical per-task run-dir
    layout), retrieval-quality metrics are computed by joining that trace
    with the task's ``ground_truth.json`` and attached to the row. Multi-
    record files are left without retrieval metrics, since one sibling trace
    cannot be attributed to each of several tasks.
    """
    resolved_run = run_name if run_name is not None else path.parent.name
    payload = json.loads(path.read_text())
    if isinstance(payload, Mapping):
        records: list[Mapping[str, Any]] = [payload]
    elif isinstance(payload, list):
        records = [r for r in payload if isinstance(r, Mapping)]
    else:
        raise ValueError(
            f"results.json at {path} is neither a list nor a mapping"
        )

    trace_path = path.parent / trace_name
    can_score_retrieval = (
        benchmarks_dir is not None
        and len(records) == 1
        and trace_path.is_file()
    )

    for record in records:
        repeat = record.get("repeat_index")
        repeat_idx = repeat if isinstance(repeat, int) else 0

        retrieval: IRScores | None = None
        if can_score_retrieval:
            task_id = record.get("task_id")
            if isinstance(task_id, str) and task_id:
                task_meta = record.get("task_metadata")
                suite = None
                if isinstance(task_meta, Mapping):
                    suite_val = task_meta.get("suite")
                    suite = suite_val if isinstance(suite_val, str) else None
                gt_path = resolve_ground_truth(
                    Path(benchmarks_dir), task_id, suite
                )
                if gt_path is not None:
                    retrieval = compute_run_ir_scores(
                        trace_path, gt_path, task_id, resolved_run
                    )

        yield metrics_from_eb_record(
            record,
            run_name=resolved_run,
            repeat_index=repeat_idx,
            retrieval=retrieval,
        )
