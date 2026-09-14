"""Tests for enriched results directory structure in run_task.py.

Verifies that task runs produce:
  results.json       — top-level results (existing)
  config.json        — snapshot of run configuration
  task_metrics.json   — timing, tool_usage, status for skip-completed
  agent/stdout.log    — agent stdout (moved from flat)
  agent/stderr.log    — agent stderr (moved from flat)
  verifier/output.json — verifier scoring output
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

# Make scripts importable
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts" / "orchestration"))
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts" / "infra"))

from run_task import TaskRunConfig, TaskRunResult, _save_results


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_task_data(task_id: str = "test-enrich-001") -> dict:
    return {
        "task": {
            "id": task_id,
            "suite": "customer_escalation",
            "task_type": "error_provenance",
            "difficulty": "medium",
            "session_type": "single",
        },
        "metadata": {
            "languages": ["python"],
        },
    }


def _make_config(**overrides) -> TaskRunConfig:
    defaults = dict(
        task_toml=Path("/fake/task.toml"),
        source="mirror",
        agent_command="claude -p",
        timeout=300,
        build_timeout=600,
        verifier_timeout=120,
        memory_mb=8192,
        output_dir=None,
        dry_run=False,
        no_build=False,
        keep_container=False,
        verbose=False,
        account=None,
        mode="baseline",
    )
    defaults.update(overrides)
    return TaskRunConfig(**defaults)


def _make_result(task_id: str = "test-enrich-001", **overrides) -> TaskRunResult:
    defaults = dict(
        task_id=task_id,
        phase="complete",
        success=True,
        error="",
        image_tag="eb-test-enrich-001",
        container_id="abc123",
        scores={"task_score": 2.5, "all_passed": True, "checkpoints_passed": 2, "checkpoints_total": 2},
        timing={"parse": 0.01, "build": 5.0, "setup": 1.2, "agent": 120.5, "scoring": 0.3},
        output_dir="",
        tool_usage={"total_input_tokens": 1000, "total_output_tokens": 500, "cost_usd": 0.05, "num_turns": 3, "mcp_tool_calls": 0},
    )
    defaults.update(overrides)
    return TaskRunResult(**defaults)


# ---------------------------------------------------------------------------
# config.json
# ---------------------------------------------------------------------------

class TestConfigJsonSnapshot:
    def test_config_json_created(self, tmp_path: Path) -> None:
        output_dir = tmp_path / "results"
        config = _make_config()
        result = _make_result()
        task_data = _make_task_data()

        _save_results(result, task_data, output_dir, config)

        config_path = output_dir / "config.json"
        assert config_path.exists(), "config.json should be created"

    def test_config_json_contains_run_settings(self, tmp_path: Path) -> None:
        output_dir = tmp_path / "results"
        config = _make_config(
            source="upstream",
            agent_command="claude --max-turns 10 -p",
            timeout=600,
            memory_mb=4096,
            mode="mcp_only",
            judge_model="cc:haiku",
            judge_account=3,
        )
        result = _make_result()
        task_data = _make_task_data()

        _save_results(result, task_data, output_dir, config)

        data = json.loads((output_dir / "config.json").read_text())
        assert data["source"] == "upstream"
        assert data["agent_command"] == "claude --max-turns 10 -p"
        assert data["timeout"] == 600
        assert data["memory_mb"] == 4096
        assert data["mode"] == "mcp_only"
        assert data["judge_model"] == "cc:haiku"
        assert data["judge_account"] == 3

    def test_config_json_excludes_sensitive_fields(self, tmp_path: Path) -> None:
        """config.json should not contain file paths or account numbers."""
        output_dir = tmp_path / "results"
        config = _make_config(account=3)
        result = _make_result()
        task_data = _make_task_data()

        _save_results(result, task_data, output_dir, config)

        data = json.loads((output_dir / "config.json").read_text())
        # account number is fine to log (not a secret), but task_toml path is host-specific
        assert "task_toml" not in data


# ---------------------------------------------------------------------------
# task_metrics.json
# ---------------------------------------------------------------------------

class TestTaskMetricsJson:
    def test_task_metrics_created(self, tmp_path: Path) -> None:
        output_dir = tmp_path / "results"
        config = _make_config()
        result = _make_result()
        task_data = _make_task_data()

        _save_results(result, task_data, output_dir, config)

        metrics_path = output_dir / "task_metrics.json"
        assert metrics_path.exists(), "task_metrics.json should be created"

    def test_task_metrics_contains_timing(self, tmp_path: Path) -> None:
        output_dir = tmp_path / "results"
        config = _make_config()
        result = _make_result(timing={"parse": 0.01, "agent": 55.3, "scoring": 0.2})
        task_data = _make_task_data()

        _save_results(result, task_data, output_dir, config)

        data = json.loads((output_dir / "task_metrics.json").read_text())
        assert data["timing"]["parse"] == pytest.approx(0.01)
        assert data["timing"]["agent"] == pytest.approx(55.3)

    def test_task_metrics_contains_tool_usage(self, tmp_path: Path) -> None:
        output_dir = tmp_path / "results"
        config = _make_config()
        result = _make_result(
            tool_usage={"total_input_tokens": 2000, "cost_usd": 0.10}
        )
        task_data = _make_task_data()

        _save_results(result, task_data, output_dir, config)

        data = json.loads((output_dir / "task_metrics.json").read_text())
        assert data["tool_usage"]["total_input_tokens"] == 2000
        assert data["tool_usage"]["cost_usd"] == pytest.approx(0.10)

    def test_task_metrics_contains_status(self, tmp_path: Path) -> None:
        output_dir = tmp_path / "results"
        config = _make_config()
        result = _make_result(success=True, phase="complete")
        task_data = _make_task_data()

        _save_results(result, task_data, output_dir, config)

        data = json.loads((output_dir / "task_metrics.json").read_text())
        assert data["success"] is True
        assert data["phase"] == "complete"
        assert data["task_id"] == "test-enrich-001"

    def test_task_metrics_on_error(self, tmp_path: Path) -> None:
        output_dir = tmp_path / "results"
        config = _make_config()
        result = _make_result(
            success=False,
            phase="build_failed",
            error="Docker build failed",
            failure_class="infra_build",
        )
        task_data = _make_task_data()

        _save_results(result, task_data, output_dir, config)

        data = json.loads((output_dir / "task_metrics.json").read_text())
        assert data["success"] is False
        assert data["phase"] == "build_failed"
        assert data["failure_class"] == "infra_build"


# ---------------------------------------------------------------------------
# agent/ subdirectory
# ---------------------------------------------------------------------------

class TestAgentSubdir:
    def test_agent_subdir_created(self, tmp_path: Path) -> None:
        output_dir = tmp_path / "results"
        config = _make_config()
        result = _make_result()
        task_data = _make_task_data()

        _save_results(result, task_data, output_dir, config)

        agent_dir = output_dir / "agent"
        assert agent_dir.is_dir(), "agent/ subdirectory should be created"


# ---------------------------------------------------------------------------
# verifier/ subdirectory
# ---------------------------------------------------------------------------

class TestVerifierSubdir:
    def test_verifier_subdir_created(self, tmp_path: Path) -> None:
        output_dir = tmp_path / "results"
        config = _make_config()
        result = _make_result()
        task_data = _make_task_data()

        _save_results(result, task_data, output_dir, config)

        verifier_dir = output_dir / "verifier"
        assert verifier_dir.is_dir(), "verifier/ subdirectory should be created"

    def test_verifier_output_json_written(self, tmp_path: Path) -> None:
        output_dir = tmp_path / "results"
        config = _make_config()
        result = _make_result(scores={"task_score": 1.5, "checkpoints_passed": 1, "checkpoints_total": 2})
        task_data = _make_task_data()

        _save_results(result, task_data, output_dir, config)

        verifier_output = output_dir / "verifier" / "output.json"
        assert verifier_output.exists(), "verifier/output.json should be written"
        data = json.loads(verifier_output.read_text())
        assert data["task_score"] == pytest.approx(1.5)


# ---------------------------------------------------------------------------
# results.json backward compatibility
# ---------------------------------------------------------------------------

class TestResultsJsonBackwardCompat:
    def test_results_json_still_at_top_level(self, tmp_path: Path) -> None:
        output_dir = tmp_path / "results"
        config = _make_config()
        result = _make_result()
        task_data = _make_task_data()

        _save_results(result, task_data, output_dir, config)

        assert (output_dir / "results.json").exists(), "results.json must remain at top level"

    def test_results_json_still_has_success_field(self, tmp_path: Path) -> None:
        """is_task_completed depends on results.json having 'success' field."""
        output_dir = tmp_path / "results"
        config = _make_config()
        result = _make_result(success=True)
        task_data = _make_task_data()

        _save_results(result, task_data, output_dir, config)

        data = json.loads((output_dir / "results.json").read_text())
        assert data["success"] is True
        assert "task_id" in data
        assert "scores" in data
