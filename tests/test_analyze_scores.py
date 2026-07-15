"""Tests for scripts/analyze_scores.py."""

from __future__ import annotations

import json
import math
from pathlib import Path

import pytest

# Ensure the scripts directory is importable
import sys

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))

from analyze_scores import (
    Checkpoint,
    TaskResult,
    _compute_delta,
    _dist_stats,
    _statistical_tests,
    calibration_bias,
    infer_mode,
    load_all_results,
    load_task_metadata_from_toml,
    mcp_deltas,
    parse_result,
    per_task_summary,
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_result(
    task_id: str = "test-task-001",
    mode: str = "baseline",
    task_score: float = 2.0,
    checkpoints_total: int = 3,
    all_passed: bool = False,
    checkpoints_passed: int = 2,
    suite: str = "customer_escalation",
    task_type: str = "error_provenance",
    difficulty: str = "medium",
) -> TaskResult:
    normalized = task_score / checkpoints_total if checkpoints_total else 0.0
    return TaskResult(
        task_id=task_id,
        mode=mode,
        success=True,
        task_score=task_score,
        normalized_score=normalized,
        all_passed=all_passed,
        checkpoints_passed=checkpoints_passed,
        checkpoints_total=checkpoints_total,
        checkpoints=(
            Checkpoint("cp1", 1.0, 1.0, True),
            Checkpoint("cp2", 1.0, 1.0, True),
            Checkpoint("cp3", 1.0, 0.0, False),
        ),
        suite=suite,
        task_type=task_type,
        difficulty=difficulty,
        languages=("python",),
        agent_time=100.0,
        source_path="/tmp/test",
    )


def _write_results_json(path: Path, **overrides: object) -> None:
    """Write a minimal results.json to the given path."""
    data = {
        "task_id": overrides.get("task_id", "test-task-001"),
        "success": True,
        "scores": {
            "task_score": overrides.get("task_score", 2.0),
            "all_passed": overrides.get("all_passed", False),
            "checkpoints_passed": overrides.get("checkpoints_passed", 2),
            "checkpoints_total": overrides.get("checkpoints_total", 3),
            "checkpoints": overrides.get(
                "checkpoints",
                [
                    {"name": "cp1", "weight": 1.0, "score": 1.0, "passed": True},
                    {"name": "cp2", "weight": 1.0, "score": 1.0, "passed": True},
                    {"name": "cp3", "weight": 1.0, "score": 0.0, "passed": False},
                ],
            ),
        },
        "timing": {"agent": 100.0},
        "task_metadata": {
            "suite": overrides.get("suite", "customer_escalation"),
            "task_type": overrides.get("task_type", "error_provenance"),
            "difficulty": overrides.get("difficulty", "medium"),
            "languages": ["python"],
        },
    }
    if "config" in overrides:
        data["config"] = overrides["config"]
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data))


# ---------------------------------------------------------------------------
# Score normalization
# ---------------------------------------------------------------------------


class TestScoreNormalization:
    def test_basic_normalization(self):
        r = _make_result(task_score=2.0, checkpoints_total=4)
        assert r.normalized_score == pytest.approx(0.5)

    def test_perfect_score(self):
        r = _make_result(task_score=3.0, checkpoints_total=3, all_passed=True)
        assert r.normalized_score == pytest.approx(1.0)

    def test_zero_score(self):
        r = _make_result(task_score=0.0, checkpoints_total=5)
        assert r.normalized_score == pytest.approx(0.0)

    def test_partial_score(self):
        r = _make_result(task_score=1.5, checkpoints_total=3)
        assert r.normalized_score == pytest.approx(0.5)


# ---------------------------------------------------------------------------
# Deduplication
# ---------------------------------------------------------------------------


class TestDeduplication:
    def test_keeps_highest_score(self, tmp_path: Path):
        """When same task_id+mode appears twice, keep the higher score."""
        dir1 = tmp_path / "run1"
        dir2 = tmp_path / "run2"
        benchmarks = tmp_path / "benchmarks"
        benchmarks.mkdir()

        _write_results_json(dir1 / "test-task-001" / "results.json", task_score=1.0)
        _write_results_json(dir2 / "test-task-001" / "results.json", task_score=2.0)

        results = load_all_results([dir1, dir2], benchmarks)
        assert len(results) == 1
        assert results[0].normalized_score == pytest.approx(2.0 / 3)

    def test_different_modes_kept(self, tmp_path: Path):
        """Different modes for same task are NOT deduplicated."""
        dir1 = tmp_path / "runs"
        benchmarks = tmp_path / "benchmarks"
        benchmarks.mkdir()

        _write_results_json(
            dir1 / "test-task-001" / "results.json",
            task_score=1.0,
        )
        _write_results_json(
            dir1 / "test-task-001_hybrid" / "results.json",
            task_score=2.0,
            config={"mode": "hybrid"},
        )

        results = load_all_results([dir1], benchmarks)
        assert len(results) == 2


# ---------------------------------------------------------------------------
# MCP delta computation
# ---------------------------------------------------------------------------


class TestMCPDelta:
    def test_basic_delta(self):
        results = [
            _make_result(
                task_id="t1", mode="baseline", task_score=1.0, checkpoints_total=2
            ),
            _make_result(
                task_id="t1", mode="hybrid", task_score=2.0, checkpoints_total=2
            ),
            _make_result(
                task_id="t2", mode="baseline", task_score=2.0, checkpoints_total=2
            ),
            _make_result(
                task_id="t2", mode="hybrid", task_score=2.0, checkpoints_total=2
            ),
        ]
        delta = _compute_delta(results, "hybrid")
        assert delta["n_paired"] == 2
        assert delta["mean_delta"] == pytest.approx(0.25)
        assert delta["pct_improved"] == pytest.approx(0.5)
        assert delta["pct_unchanged"] == pytest.approx(0.5)
        assert delta["pct_degraded"] == pytest.approx(0.0)

    def test_no_pairs(self):
        results = [
            _make_result(task_id="t1", mode="baseline"),
            _make_result(task_id="t2", mode="hybrid"),
        ]
        delta = _compute_delta(results, "hybrid")
        assert delta["n_paired"] == 0

    def test_degradation(self):
        results = [
            _make_result(
                task_id="t1", mode="baseline", task_score=3.0, checkpoints_total=3
            ),
            _make_result(
                task_id="t1", mode="mcp_only", task_score=1.0, checkpoints_total=3
            ),
        ]
        delta = _compute_delta(results, "mcp_only")
        assert delta["n_paired"] == 1
        assert delta["mean_delta"] < 0
        assert delta["pct_degraded"] == pytest.approx(1.0)


# ---------------------------------------------------------------------------
# Calibration bias detection
# ---------------------------------------------------------------------------


class TestCalibrationBias:
    def test_no_bias(self):
        results = [
            _make_result(
                task_id="cal-test-001",
                mode="baseline",
                task_score=2.0,
                checkpoints_total=3,
            ),
            _make_result(
                task_id="cal-test-001",
                mode="hybrid",
                task_score=2.0,
                checkpoints_total=3,
            ),
        ]
        cb = calibration_bias(results)
        assert cb["calibration_task_count"] == 2
        assert cb["bias_flagged"] is False
        assert cb["max_mode_delta"] == pytest.approx(0.0)

    def test_bias_flagged(self):
        results = [
            _make_result(
                task_id="cal-test-001",
                mode="baseline",
                task_score=1.0,
                checkpoints_total=3,
            ),
            _make_result(
                task_id="cal-test-001",
                mode="hybrid",
                task_score=3.0,
                checkpoints_total=3,
            ),
        ]
        cb = calibration_bias(results, bias_threshold=0.10)
        assert cb["bias_flagged"] is True
        # delta = 1.0 - 0.333 = 0.667
        assert cb["max_mode_delta"] > 0.10

    def test_no_calibration_tasks(self):
        results = [_make_result(task_id="normal-001")]
        cb = calibration_bias(results)
        assert cb["calibration_task_count"] == 0
        assert cb["bias_flagged"] is False


# ---------------------------------------------------------------------------
# Statistical tests
# ---------------------------------------------------------------------------


class TestStatisticalTests:
    def test_cohens_d_zero_when_identical(self):
        result = _statistical_tests([0.5, 0.6, 0.7], [0.5, 0.6, 0.7])
        assert result["cohens_d"] == pytest.approx(0.0)

    def test_cohens_d_positive_when_improved(self):
        result = _statistical_tests(
            [0.3, 0.4, 0.5, 0.6, 0.3, 0.4],
            [0.5, 0.6, 0.7, 0.8, 0.5, 0.6],
        )
        assert result["cohens_d"] > 0

    def test_graceful_without_scipy(self, monkeypatch: pytest.MonkeyPatch):
        """If scipy import fails, should still return results with None."""
        import builtins

        real_import = builtins.__import__

        def mock_import(name, *args, **kwargs):
            if "scipy" in name:
                raise ImportError("no scipy")
            return real_import(name, *args, **kwargs)

        monkeypatch.setattr(builtins, "__import__", mock_import)
        result = _statistical_tests([0.5, 0.6], [0.7, 0.8])
        assert result["wilcoxon_p"] is None


# ---------------------------------------------------------------------------
# Mode inference
# ---------------------------------------------------------------------------


class TestModeInference:
    def test_config_mode_takes_precedence(self, tmp_path: Path):
        path = tmp_path / "some_dir" / "results.json"
        data = {"config": {"mode": "hybrid"}}
        assert infer_mode(path, data) == "hybrid"

    def test_dirname_suffix_hybrid(self, tmp_path: Path):
        path = tmp_path / "mcp_batch" / "task-001_hybrid" / "results.json"
        data = {}
        assert infer_mode(path, data) == "hybrid"

    def test_dirname_suffix_mcp_only(self, tmp_path: Path):
        path = tmp_path / "mcp_batch" / "task-001_mcp_only" / "results.json"
        data = {}
        assert infer_mode(path, data) == "mcp_only"

    def test_runs_defaults_baseline(self, tmp_path: Path):
        path = tmp_path / "results" / "runs" / "task-001" / "results.json"
        data = {}
        assert infer_mode(path, data) == "baseline"

    def test_smoke_hybrid(self, tmp_path: Path):
        path = tmp_path / "results" / "smoke_hybrid_v2" / "results.json"
        data = {}
        assert infer_mode(path, data) == "hybrid"

    def test_smoke_mcp(self, tmp_path: Path):
        path = tmp_path / "results" / "smoke_mcp" / "results.json"
        data = {}
        assert infer_mode(path, data) == "mcp_only"


# ---------------------------------------------------------------------------
# Metadata fallback to task.toml
# ---------------------------------------------------------------------------


class TestMetadataFallback:
    def test_loads_from_toml(self, tmp_path: Path):
        task_dir = tmp_path / "benchmarks" / "some_suite" / "my-task-001"
        task_dir.mkdir(parents=True)
        (task_dir / "task.toml").write_text(
            "[task]\n"
            'id = "my-task-001"\n'
            'suite = "feature_delivery"\n'
            'task_type = "monorepo_boundary"\n'
            'difficulty = "hard"\n'
            "\n"
            "[metadata]\n"
            'languages = ["go", "python"]\n'
        )
        meta = load_task_metadata_from_toml("my-task-001", tmp_path / "benchmarks")
        assert meta["suite"] == "feature_delivery"
        assert meta["task_type"] == "monorepo_boundary"
        assert meta["difficulty"] == "hard"
        assert meta["languages"] == ["go", "python"]

    def test_returns_empty_when_not_found(self, tmp_path: Path):
        benchmarks = tmp_path / "benchmarks"
        benchmarks.mkdir()
        meta = load_task_metadata_from_toml("nonexistent", benchmarks)
        assert meta == {}

    def test_parse_result_falls_back_to_toml(self, tmp_path: Path):
        """If results.json has no task_metadata, fall back to task.toml."""
        # Write results.json without task_metadata
        results_dir = tmp_path / "runs" / "my-task-001"
        results_dir.mkdir(parents=True)
        data = {
            "task_id": "my-task-001",
            "success": True,
            "scores": {
                "task_score": 1.0,
                "all_passed": False,
                "checkpoints_passed": 1,
                "checkpoints_total": 2,
                "checkpoints": [
                    {"name": "cp1", "weight": 1.0, "score": 1.0, "passed": True},
                    {"name": "cp2", "weight": 1.0, "score": 0.0, "passed": False},
                ],
            },
            "timing": {"agent": 50.0},
        }
        (results_dir / "results.json").write_text(json.dumps(data))

        # Write task.toml
        task_dir = tmp_path / "benchmarks" / "suite" / "my-task-001"
        task_dir.mkdir(parents=True)
        (task_dir / "task.toml").write_text(
            "[task]\n"
            'suite = "technical_debt"\n'
            'task_type = "dead_code_necropsy"\n'
            'difficulty = "easy"\n'
            "[metadata]\n"
            'languages = ["java"]\n'
        )

        result = parse_result(
            results_dir / "results.json",
            tmp_path / "benchmarks",
        )
        assert result is not None
        assert result.suite == "technical_debt"
        assert result.task_type == "dead_code_necropsy"
        assert result.difficulty == "easy"


# ---------------------------------------------------------------------------
# Distribution stats
# ---------------------------------------------------------------------------


class TestDistStats:
    def test_empty_list(self):
        stats = _dist_stats([])
        assert stats["count"] == 0
        assert stats["mean"] is None

    def test_single_result(self):
        r = _make_result(task_score=2.0, checkpoints_total=4)
        stats = _dist_stats([r])
        assert stats["count"] == 1
        assert stats["mean"] == pytest.approx(0.5)
        assert stats["std"] == 0.0

    def test_multiple_results(self):
        results = [
            _make_result(task_id=f"t{i}", task_score=float(i), checkpoints_total=3)
            for i in range(4)
        ]
        stats = _dist_stats(results)
        assert stats["count"] == 4
        assert stats["min"] == pytest.approx(0.0)
        assert stats["max"] == pytest.approx(1.0)


# ---------------------------------------------------------------------------
# Per-task summary
# ---------------------------------------------------------------------------


class TestPerTaskSummary:
    def test_cross_mode_summary(self):
        results = [
            _make_result(
                task_id="t1", mode="baseline", task_score=1.0, checkpoints_total=2
            ),
            _make_result(
                task_id="t1", mode="hybrid", task_score=2.0, checkpoints_total=2
            ),
        ]
        summary = per_task_summary(results)
        assert len(summary) == 1
        assert summary[0]["task_id"] == "t1"
        assert "baseline" in summary[0]["scores"]
        assert "hybrid" in summary[0]["scores"]
        assert summary[0]["scores"]["baseline"] == pytest.approx(0.5)
        assert summary[0]["scores"]["hybrid"] == pytest.approx(1.0)

    def test_calibration_flag(self):
        results = [_make_result(task_id="cal-test-001")]
        summary = per_task_summary(results)
        assert summary[0]["is_calibration"] is True

        results2 = [_make_result(task_id="normal-001")]
        summary2 = per_task_summary(results2)
        assert summary2[0]["is_calibration"] is False


# ---------------------------------------------------------------------------
# INVALID-run exclusion
# ---------------------------------------------------------------------------


class TestInvalidRunExclusion:
    """An invalidated run must be dropped, never counted as a low score.

    ``run_task`` flags a run INVALID and then *falls through to scoring* in three
    branches (``_route_zero_mcp_run``, ``_route_integrity_violation``,
    ``_scan_mcp_config_error``), so these files carry a real-looking score. The
    zero-MCP branch is the one that bites under the mcp_only filesystem gate.
    """

    def test_lowercase_invalid_with_scores_is_dropped(self, tmp_path: Path):
        path = tmp_path / "runs" / "t1" / "mcp_only" / "results.json"
        _write_results_json(path, task_id="t1")
        data = json.loads(path.read_text())
        data["status"] = "invalid"  # run_task._route_zero_mcp_run
        data["phase"] = "agent_infra_error"
        data["success"] = False
        path.write_text(json.dumps(data))

        assert parse_result(path, tmp_path / "benchmarks") is None

    def test_uppercase_invalid_is_dropped(self, tmp_path: Path):
        """The verifier schema writes uppercase; the fold must catch it."""
        path = tmp_path / "runs" / "t1" / "mcp_only" / "results.json"
        _write_results_json(path, task_id="t1")
        data = json.loads(path.read_text())
        data["status"] = "INVALID"
        path.write_text(json.dumps(data))

        assert parse_result(path, tmp_path / "benchmarks") is None

    def test_uppercase_valid_is_kept(self, tmp_path: Path):
        """9 results.json on disk carry status='VALID' — they must still count."""
        path = tmp_path / "runs" / "t1" / "baseline" / "results.json"
        _write_results_json(path, task_id="t1")
        data = json.loads(path.read_text())
        data["status"] = "VALID"
        path.write_text(json.dumps(data))

        tr = parse_result(path, tmp_path / "benchmarks")
        assert tr is not None
        assert tr.normalized_score == pytest.approx(2.0 / 3.0)

    def test_legacy_result_without_status_is_kept(self, tmp_path: Path):
        """84 of 93 results on disk predate the status field."""
        path = tmp_path / "runs" / "t1" / "baseline" / "results.json"
        _write_results_json(path, task_id="t1")  # no status, success=True
        tr = parse_result(path, tmp_path / "benchmarks")
        assert tr is not None

    def test_status_derived_when_absent(self, tmp_path: Path):
        """A failed legacy run is INVALID even though it never stamped status."""
        path = tmp_path / "runs" / "t1" / "baseline" / "results.json"
        _write_results_json(path, task_id="t1")
        data = json.loads(path.read_text())
        data["success"] = False
        data["phase"] = "error"
        path.write_text(json.dumps(data))

        assert parse_result(path, tmp_path / "benchmarks") is None

    def test_invalid_run_does_not_enter_the_mean(self, tmp_path: Path):
        """End-to-end: the arm's mean must not move because a run was invalid."""
        good = tmp_path / "runs" / "t1" / "mcp_only" / "results.json"
        _write_results_json(good, task_id="t1", task_score=3.0, checkpoints_total=3)

        bad = tmp_path / "runs" / "t2" / "mcp_only" / "results.json"
        _write_results_json(bad, task_id="t2", task_score=0.0, checkpoints_total=3)
        data = json.loads(bad.read_text())
        data["status"] = "invalid"
        data["phase"] = "agent_infra_error"
        data["success"] = False
        bad.write_text(json.dumps(data))

        results = load_all_results([tmp_path / "runs"], tmp_path / "benchmarks")
        assert [r.task_id for r in results] == ["t1"]
        assert _dist_stats(results)["mean"] == pytest.approx(1.0)
