"""Tests for the EnterpriseBench → codeprobe trace-quality adapter."""

from __future__ import annotations

import dataclasses
import json
from pathlib import Path
from typing import Any

import pytest

# The adapter under test is the one eb_metrics module that needs codeprobe, which
# is a sibling rig CI cannot install. Skip rather than error at collection —
# pytest aborts the whole run on a collection error, taking the 3200 tests that
# do not need codeprobe with it.
pytest.importorskip("codeprobe", reason="the trace-quality adapter is codeprobe-backed")

from eb_metrics.aggregate_quality import build_quality_metrics  # noqa: E402
from eb_metrics.ir_metrics import compute_ir_scores  # noqa: E402
from eb_metrics.trace_quality_adapter import (  # noqa: E402
    metrics_from_eb_record,
    metrics_from_results_file,
)

REPO_ROOT = Path(__file__).resolve().parents[2]
FIXTURES = REPO_ROOT / "tests" / "fixtures" / "eb_results"
SMOKE_FIXTURE = REPO_ROOT / "results" / "smoke_mcp" / "results.json"


def _record(
    *,
    task_id: str = "t-001",
    success: bool = True,
    phase: str = "complete",
    failure_class: str | None = None,
    error: str = "",
    task_score: float = 1.0,
    all_passed: bool = True,
    checkpoints_passed: int = 1,
    checkpoints_total: int = 1,
    checkpoints: list[dict[str, Any]] | None = None,
    suite: str = "platform_engineering",
    task_type: str = "config_drift",
) -> dict[str, Any]:
    if checkpoints is None:
        checkpoints = [
            {
                "name": "cp1",
                "weight": 1.0,
                "score": 1.0 if all_passed else 0.0,
                "passed": all_passed,
                "duration_ms": 1,
                "exit_code": 0,
            }
        ]
    return {
        "task_id": task_id,
        "success": success,
        "phase": phase,
        "error": error,
        "failure_class": failure_class,
        "scores": {
            "task_score": task_score,
            "all_passed": all_passed,
            "checkpoints_passed": checkpoints_passed,
            "checkpoints_total": checkpoints_total,
            "checkpoints": checkpoints,
        },
        "task_metadata": {"suite": suite, "task_type": task_type},
    }


# ---------------------------------------------------------------------------
# Per-record adapter
# ---------------------------------------------------------------------------


def test_clean_record_is_valid_with_full_precision() -> None:
    rec = _record(
        task_id="clean-001",
        all_passed=True,
        checkpoints_passed=3,
        checkpoints_total=3,
        checkpoints=[
            {"name": "a", "passed": True},
            {"name": "b", "passed": True},
            {"name": "c", "passed": True},
        ],
        task_score=3.0,
    )
    m = metrics_from_eb_record(rec, run_name="baseline")

    assert m.validity == "valid"
    assert m.validity_reason is None
    assert m.score == 3.0
    assert m.scorer_passed is True
    assert m.precision == 1.0
    assert m.recall is None
    assert m.f1 is None
    assert m.quality_flags == ()
    assert m.config_label == "baseline"
    assert m.detail == {
        "phase": "complete",
        "suite": "platform_engineering",
        "task_type": "config_drift",
    }


def test_build_failed_record_is_invalid_with_failure_class_flag() -> None:
    rec = _record(
        task_id="bf-001",
        success=False,
        phase="build_failed",
        failure_class="infra_build",
        error="docker build returned non-zero",
        all_passed=False,
        checkpoints_passed=0,
        checkpoints_total=0,
        checkpoints=[],
        task_score=0.0,
    )
    m = metrics_from_eb_record(rec, run_name="baseline")

    assert m.validity == "invalid"
    assert m.validity_reason == "infra_build"
    # Score is suppressed when the trial never ran end-to-end.
    assert m.score is None
    # No checkpoints → precision is undefined, not zero.
    assert m.precision is None
    # Flags include both the validity marker and the failure_class label,
    # matching codeprobe's flag-histogram conventions.
    assert "invalid" in m.quality_flags
    assert "infra_build" in m.quality_flags
    # No checkpoint flags emitted because the checkpoint list was empty.
    assert all(not f.startswith("checkpoint_") for f in m.quality_flags)
    assert m.detail["error"] == "docker build returned non-zero"
    assert m.detail["phase"] == "build_failed"


def test_partial_checkpoints_record_emits_per_checkpoint_flags() -> None:
    rec = _record(
        task_id="partial-001",
        all_passed=False,
        checkpoints_passed=2,
        checkpoints_total=3,
        checkpoints=[
            {"name": "cp_one", "passed": True},
            {"name": "cp_two", "passed": True},
            {"name": "cp_three", "passed": False},
        ],
        task_score=2.0,
    )
    m = metrics_from_eb_record(rec, run_name="with_mcp")

    assert m.validity == "valid"
    # phase=complete + success=true → still valid even though scoring oracle failed.
    assert pytest.approx(m.precision) == 2 / 3
    assert m.scorer_passed is False
    # Only the failing checkpoint contributes a flag.
    assert "checkpoint_cp_three_failed" in m.quality_flags
    assert "checkpoint_cp_one_failed" not in m.quality_flags
    assert "checkpoint_cp_two_failed" not in m.quality_flags
    # No "invalid" flag — validity is still valid.
    assert "invalid" not in m.quality_flags


def test_phase_complete_with_success_false_is_invalid_with_phase_reason() -> None:
    """If success is false the trial is invalid even when phase is 'complete'.

    failure_class is null here, so validity_reason falls back to the
    phase string. This guards the "do not invent values" rule — we read
    the existing fields and never synthesize a reason.
    """
    rec = _record(
        task_id="bad-success-001",
        success=False,
        phase="complete",
        failure_class=None,
        all_passed=False,
        checkpoints_passed=0,
        checkpoints_total=1,
    )
    m = metrics_from_eb_record(rec, run_name="baseline")
    assert m.validity == "invalid"
    assert m.validity_reason == "complete"
    assert "invalid" in m.quality_flags


def test_missing_task_id_raises() -> None:
    with pytest.raises(ValueError):
        metrics_from_eb_record({"success": True, "phase": "complete"}, run_name="x")


# ---------------------------------------------------------------------------
# Retrieval-axis wiring (recall / f1 / low_recall)
# ---------------------------------------------------------------------------


def test_retrieval_populates_recall_f1_and_flags_low_recall() -> None:
    # 1 of 3 required found → file_recall 1/3 (< 0.5 → low_recall);
    # all 1 retrieved is relevant → context_efficiency 1.0 → f1 = 0.5.
    ir = compute_ir_scores(
        ["pkg/a.go"], ["pkg/a.go", "pkg/b.go", "pkg/c.go"], "t", "baseline"
    )
    m = metrics_from_eb_record(_record(task_id="ret-001"), run_name="baseline", retrieval=ir)

    assert m.recall == pytest.approx(1 / 3, abs=1e-4)
    assert m.f1 == 0.5
    # precision stays the checkpoint pass-rate, untouched by the retrieval axis.
    assert m.precision == 1.0
    assert "low_recall" in m.quality_flags
    assert m.detail["retrieval_metrics"]["file_recall"] == pytest.approx(1 / 3, abs=1e-4)
    assert m.detail["retrieval_metrics"]["n_overlap"] == 1


def test_retrieval_high_recall_no_low_recall_flag() -> None:
    ir = compute_ir_scores(["pkg/a.go", "pkg/b.go"], ["pkg/a.go", "pkg/b.go"], "t", "baseline")
    m = metrics_from_eb_record(_record(task_id="ret-002"), run_name="baseline", retrieval=ir)

    assert m.recall == 1.0
    assert m.f1 == 1.0
    assert "low_recall" not in m.quality_flags


def test_no_retrieval_leaves_recall_none() -> None:
    m = metrics_from_eb_record(_record(task_id="ret-003"), run_name="baseline")
    assert m.recall is None
    assert m.f1 is None
    assert "retrieval_metrics" not in m.detail


def test_retrieval_metrics_detail_carries_localization_and_ttfr(tmp_path: Path) -> None:
    # jn73.1: localization@k and TTFR ride along in the full IRScores dict,
    # no adapter-side schema change needed to surface them.
    ir = compute_ir_scores(["pkg/a.go"], ["pkg/a.go"], "t", "baseline")
    ir = dataclasses.replace(
        ir,
        turns_to_first_relevant=3,
        tokens_to_first_relevant=1200,
        ttfr_seconds=8.5,
        ttfr_coverage="ok",
    )
    m = metrics_from_eb_record(_record(task_id="ret-004"), run_name="baseline", retrieval=ir)

    rm = m.detail["retrieval_metrics"]
    assert rm["localization"] == {1: 1.0, 3: 1.0, 5: 1.0, 10: 1.0}
    assert rm["turns_to_first_relevant"] == 3
    assert rm["tokens_to_first_relevant"] == 1200
    assert rm["ttfr_seconds"] == 8.5
    assert rm["ttfr_coverage"] == "ok"
    assert "ttfr_unavailable" not in m.quality_flags


def test_retrieval_ttfr_no_tool_calls_flags_observability_gap() -> None:
    # An observability gap (agent made zero tool calls) is distinct from a
    # genuine "found nothing" miss -- it gets its own flag, not low_recall
    # alone, so it's not misread as the agent having tried and failed.
    ir = compute_ir_scores([], ["pkg/a.go"], "t", "baseline")
    ir = dataclasses.replace(ir, ttfr_coverage="no_tool_calls")
    m = metrics_from_eb_record(_record(task_id="ret-005"), run_name="baseline", retrieval=ir)
    assert "ttfr_unavailable" in m.quality_flags


def test_retrieval_ttfr_genuine_miss_does_not_flag_unavailable() -> None:
    # coverage == "not_found" is a real signal (the agent tried and missed),
    # not an observability gap -- must NOT get the ttfr_unavailable flag.
    ir = compute_ir_scores(["pkg/other.go"], ["pkg/a.go"], "t", "baseline")
    ir = dataclasses.replace(ir, ttfr_coverage="not_found")
    m = metrics_from_eb_record(_record(task_id="ret-006"), run_name="baseline", retrieval=ir)
    assert "ttfr_unavailable" not in m.quality_flags


def _trace_line(name: str, inp: dict) -> str:
    return json.dumps(
        {
            "type": "assistant",
            "message": {
                "content": [{"type": "tool_use", "id": "x1", "name": name, "input": inp}]
            },
        }
    )


def test_metrics_from_results_file_computes_retrieval_end_to_end(tmp_path: Path) -> None:
    run_dir = tmp_path / "results" / "baseline"
    run_dir.mkdir(parents=True)
    (run_dir / "results.json").write_text(
        json.dumps(
            _record(task_id="eb-e2e-001", suite="dependency_management", task_type="dependency_graph")
        )
    )
    (run_dir / "agent_trace.jsonl").write_text(
        _trace_line("Read", {"file_path": "/workspace/kubernetes/pkg/kubelet/prober/worker.go"})
        + "\n"
    )
    bench = tmp_path / "benchmarks"
    task_dir = bench / "dependency_management" / "eb-e2e-001"
    task_dir.mkdir(parents=True)
    (task_dir / "ground_truth.json").write_text(
        json.dumps(
            {
                "ground_truth": {
                    "required_files": [
                        {"path": "pkg/kubelet/prober/worker.go", "repo": "kubernetes"}
                    ]
                }
            }
        )
    )

    rows = list(
        metrics_from_results_file(run_dir / "results.json", benchmarks_dir=bench)
    )
    assert len(rows) == 1
    assert rows[0].recall == 1.0
    assert rows[0].detail["retrieval_metrics"]["n_overlap"] == 1


def test_metrics_from_results_file_no_benchmarks_dir_leaves_recall_none(tmp_path: Path) -> None:
    run_dir = tmp_path / "results" / "baseline"
    run_dir.mkdir(parents=True)
    (run_dir / "results.json").write_text(json.dumps(_record(task_id="eb-e2e-002")))
    (run_dir / "agent_trace.jsonl").write_text(
        _trace_line("Read", {"file_path": "/workspace/kubernetes/pkg/a.go"}) + "\n"
    )
    rows = list(metrics_from_results_file(run_dir / "results.json"))
    assert rows[0].recall is None


# ---------------------------------------------------------------------------
# Integration — synthetic fixtures
# ---------------------------------------------------------------------------


def test_metrics_from_results_file_round_trip(tmp_path: Path) -> None:
    rows = list(metrics_from_results_file(FIXTURES / "baseline" / "results.json"))
    assert [r.task_id for r in rows] == [
        "synthetic-clean-001",
        "synthetic-build-failed-001",
    ]
    assert rows[0].validity == "valid"
    assert rows[1].validity == "invalid"
    assert rows[0].config_label == "baseline"
    assert rows[1].config_label == "baseline"


def test_build_quality_metrics_two_run_fixture() -> None:
    payload = build_quality_metrics(FIXTURES)

    assert payload["schema_version"] == 1
    overall = payload["overall"]
    assert overall["total_trials"] == 4
    # 2 valid (clean + partial-checkpoints) and 2 invalid (build-failed + agent_error)
    assert overall["valid_trials"] == 2
    assert overall["invalid_trials"] == 2
    # Every trial that has any flag (or is invalid) is low-quality, so the
    # only "high quality" trial is the clean one.
    assert overall["low_quality_trials"] == 3

    flag_counts = overall["flag_counts"]
    assert flag_counts.get("invalid") == 2
    assert flag_counts.get("infra_build") == 1
    assert flag_counts.get("agent_error") == 1
    assert flag_counts.get("checkpoint_cp3_failed") == 1
    assert flag_counts.get("checkpoint_cp1_failed") == 1
    assert flag_counts.get("checkpoint_cp2_failed") == 1

    assert set(payload["per_config"]) == {"baseline", "with_mcp"}
    assert payload["per_config"]["baseline"]["total_trials"] == 2
    assert payload["per_config"]["with_mcp"]["total_trials"] == 2

    low = payload["low_quality_trials"]
    assert {row["task_id"] for row in low} == {
        "synthetic-build-failed-001",
        "synthetic-partial-001",
        "synthetic-agent-error-001",
    }


def test_build_quality_metrics_emits_top_level_schema_keys() -> None:
    payload = build_quality_metrics(FIXTURES)
    assert set(payload.keys()) == {
        "schema_version",
        "overall",
        "per_config",
        "low_quality_trials",
    }


# ---------------------------------------------------------------------------
# Real-world snapshot — guards adapter against drift in the EB result shape
# ---------------------------------------------------------------------------


@pytest.mark.skipif(
    not SMOKE_FIXTURE.is_file(),
    reason="results/smoke_mcp/results.json not present in this checkout",
)
def test_smoke_mcp_real_fixture_assertion() -> None:
    """Lock in adapter behaviour against the canonical EB fixture.

    smoke_mcp is a single-record run (one task, phase=complete) where
    the scoring oracle failed every checkpoint — exactly the kind of
    'completed but scored zero' trial codeprobe's trace_quality model
    is meant to surface. Asserting against ``total_trials`` and the
    dominant flag count guards against accidental shape drift.
    """
    payload = build_quality_metrics(
        SMOKE_FIXTURE.parent.parent, pattern="smoke_mcp/results.json"
    )

    overall = payload["overall"]
    assert overall["total_trials"] == 1
    assert overall["valid_trials"] == 1
    assert overall["invalid_trials"] == 0
    # Every checkpoint failed in this snapshot, so each becomes a flag.
    flag_counts = overall["flag_counts"]
    assert sum(flag_counts.values()) == 3
    assert all(name.startswith("checkpoint_") for name in flag_counts)


def test_smoke_mcp_records_shape_matches_adapter_assumptions() -> None:
    """Assert the canonical fixture has the field names the adapter reads.

    Runs even when the file has been pruned — an empty / missing fixture
    is a surprise we want to catch via the file-presence assertion.
    """
    if not SMOKE_FIXTURE.is_file():
        pytest.skip("results/smoke_mcp/results.json not present")
    payload = json.loads(SMOKE_FIXTURE.read_text())
    # smoke_mcp/results.json may be a single record or a list — both are
    # legal EB output shapes and the adapter handles both.
    records = payload if isinstance(payload, list) else [payload]
    assert records, "smoke_mcp fixture must contain at least one record"
    rec = records[0]
    for field in ("task_id", "success", "phase", "scores", "task_metadata"):
        assert field in rec, f"missing required field: {field}"
    assert "checkpoints" in rec["scores"]
