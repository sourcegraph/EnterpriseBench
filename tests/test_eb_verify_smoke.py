"""
Smoke test for eb_verify: end-to-end parse → verify → score → JSON output.

Exercises at least 3 plugin types: answer, code_patch, config_validator.
"""

from __future__ import annotations

import json
import os
import subprocess
import textwrap
from pathlib import Path

import pytest

from eb_verify.task_parser import parse_task, TaskDefinition, Checkpoint
from eb_verify.runner import CheckpointRunner
from eb_verify.scoring import (
    CheckpointResult,
    VerificationResult,
    compute_score,
    write_reward,
)
from eb_verify.plugins import get_validator, list_validators


FIXTURES_DIR = Path(__file__).parent / "fixtures"


# ---------------------------------------------------------------------------
# Fixture: a synthetic task.toml with verifier scripts that actually run
# ---------------------------------------------------------------------------

SMOKE_TASK_TOML = textwrap.dedent("""\
    difficulty_stratum = "calibration"

    [task]
    id = "smoke-test-001"
    suite = "dependency_management"
    difficulty = "medium"
    session_type = "single"
    description = "Smoke test task for eb_verify"
    prompt = "Verify that the pipeline works end-to-end."

    [[repos]]
    url = "github.com/example/smoke-repo"
    rev = "v0.0.1"
    path = "smoke-repo"
    role = "primary"

    [[checkpoints]]
    name = "always_pass"
    weight = 0.40
    verifier = "checks/pass.sh"
    description = "A checkpoint that always passes"
    timeout_seconds = 10

    [[checkpoints]]
    name = "partial_score"
    weight = 0.35
    verifier = "checks/partial.sh"
    description = "A checkpoint that returns partial credit"
    timeout_seconds = 10

    [[checkpoints]]
    name = "always_fail"
    weight = 0.25
    verifier = "checks/fail.sh"
    description = "A checkpoint that always fails"
    timeout_seconds = 10

    [artifacts]
    required = ["answer", "config"]
    optional = ["code_patch"]

    [metadata]
    languages = ["python"]
    total_loc = 500
    dependency_depth = 1
""")


@pytest.fixture
def smoke_env(tmp_path: Path) -> tuple[Path, Path, Path]:
    """Set up task dir with verifier scripts and a workspace with artifacts."""
    task_dir = tmp_path / "task"
    task_dir.mkdir()

    # Write task.toml
    task_toml = task_dir / "task.toml"
    task_toml.write_text(SMOKE_TASK_TOML)

    # Create verifier scripts
    checks = task_dir / "checks"
    checks.mkdir()

    (checks / "pass.sh").write_text(
        '#!/bin/bash\necho \'{"score": 1.0, "detail": "all good"}\'\nexit 0\n'
    )
    (checks / "partial.sh").write_text(
        '#!/bin/bash\necho \'{"score": 0.6, "detail": "partial credit"}\'\nexit 0\n'
    )
    (checks / "fail.sh").write_text(
        '#!/bin/bash\necho \'{"score": 0.0, "detail": "failed check"}\'\nexit 1\n'
    )

    for script in checks.iterdir():
        script.chmod(0o755)

    # Create workspace with a mock repo and artifacts
    workspace = tmp_path / "workspace"
    workspace.mkdir()

    repo = workspace / "smoke-repo"
    repo.mkdir()
    (repo / ".git").mkdir()
    (repo / "main.py").write_text("print('hello')\n")

    # answer.json for the answer plugin
    (workspace / "answer.json").write_text(json.dumps({"answer": "42", "reasoning": "the answer"}))

    # config artifact — a valid JSON config file
    output_dir = workspace / "output"
    output_dir.mkdir()
    (output_dir / "settings.json").write_text(json.dumps({"key": "value", "debug": False}))

    return task_toml, task_dir, workspace


# ===========================================================================
# 1. Task parsing
# ===========================================================================


class TestTaskParsing:
    def test_parse_smoke_task(self, smoke_env: tuple[Path, Path, Path]) -> None:
        task_toml, _, _ = smoke_env
        task = parse_task(task_toml)

        assert task.id == "smoke-test-001"
        assert task.suite == "dependency_management"
        assert task.difficulty == "medium"
        assert task.session_type == "single"
        assert task.difficulty_stratum == "calibration"
        assert len(task.repos) == 1
        assert task.repos[0].path == "smoke-repo"
        assert len(task.checkpoints) == 3

    def test_parse_example_task(self) -> None:
        example = Path(__file__).parent.parent / "benchmarks" / "EXAMPLE_TASK.toml"
        task = parse_task(example)

        assert task.id == "dep-mgmt-grpc-proto-bump-001"
        assert task.suite == "dependency_management"
        assert task.difficulty == "hard"
        assert len(task.repos) == 2
        assert len(task.checkpoints) == 3
        assert task.ground_truth is not None
        assert len(task.ground_truth.required_files) == 3
        assert task.tool_access is not None
        assert len(task.tool_access.sourcegraph_mirrors) == 2
        assert task.csb_lineage is not None
        assert task.csb_lineage.migration_status == "verified"

    def test_checkpoint_weights_sum(self, smoke_env: tuple[Path, Path, Path]) -> None:
        task_toml, _, _ = smoke_env
        task = parse_task(task_toml)
        total = sum(cp.weight for cp in task.checkpoints)
        assert abs(total - 1.0) < 0.01

    def test_artifacts_parsed(self, smoke_env: tuple[Path, Path, Path]) -> None:
        task_toml, _, _ = smoke_env
        task = parse_task(task_toml)
        assert "answer" in task.artifacts.required
        assert "config" in task.artifacts.required
        assert "code_patch" in task.artifacts.optional


# ===========================================================================
# 2. Checkpoint execution
# ===========================================================================


class TestCheckpointExecution:
    def test_run_passing_checkpoint(self, smoke_env: tuple[Path, Path, Path]) -> None:
        task_toml, task_dir, workspace = smoke_env
        task = parse_task(task_toml)
        runner = CheckpointRunner(task=task, task_dir=task_dir, workspace=workspace)

        result = runner.run_checkpoint(task.checkpoints[0])  # always_pass
        assert result.passed is True
        assert result.score == 1.0
        assert result.name == "always_pass"

    def test_run_partial_checkpoint(self, smoke_env: tuple[Path, Path, Path]) -> None:
        task_toml, task_dir, workspace = smoke_env
        task = parse_task(task_toml)
        runner = CheckpointRunner(task=task, task_dir=task_dir, workspace=workspace)

        result = runner.run_checkpoint(task.checkpoints[1])  # partial_score
        assert result.passed is True
        assert result.score == pytest.approx(0.6)
        assert result.name == "partial_score"

    def test_run_failing_checkpoint(self, smoke_env: tuple[Path, Path, Path]) -> None:
        task_toml, task_dir, workspace = smoke_env
        task = parse_task(task_toml)
        runner = CheckpointRunner(task=task, task_dir=task_dir, workspace=workspace)

        result = runner.run_checkpoint(task.checkpoints[2])  # always_fail
        assert result.passed is False
        assert result.score == 0.0
        assert result.name == "always_fail"

    def test_missing_verifier(self, smoke_env: tuple[Path, Path, Path]) -> None:
        task_toml, task_dir, workspace = smoke_env
        task = parse_task(task_toml)
        runner = CheckpointRunner(task=task, task_dir=task_dir, workspace=workspace)

        fake_cp = Checkpoint(
            name="missing", weight=0.5, verifier="checks/nonexistent.sh"
        )
        result = runner.run_checkpoint(fake_cp)
        assert result.passed is False
        assert "not found" in result.detail

    def test_run_single_by_name(self, smoke_env: tuple[Path, Path, Path]) -> None:
        task_toml, task_dir, workspace = smoke_env
        task = parse_task(task_toml)
        runner = CheckpointRunner(task=task, task_dir=task_dir, workspace=workspace)

        result = runner.run_single("always_pass")
        assert result.passed is True

    def test_run_single_not_found(self, smoke_env: tuple[Path, Path, Path]) -> None:
        task_toml, task_dir, workspace = smoke_env
        task = parse_task(task_toml)
        runner = CheckpointRunner(task=task, task_dir=task_dir, workspace=workspace)

        with pytest.raises(ValueError, match="not found"):
            runner.run_single("nonexistent_checkpoint")


# ===========================================================================
# 3. Score aggregation
# ===========================================================================


class TestScoring:
    def test_compute_score_weighted(self) -> None:
        results = [
            CheckpointResult(name="a", weight=0.4, passed=True, score=1.0),
            CheckpointResult(name="b", weight=0.35, passed=True, score=0.6),
            CheckpointResult(name="c", weight=0.25, passed=False, score=0.0),
        ]
        score = compute_score(results)
        # (1.0*0.4 + 0.6*0.35 + 0.0*0.25) / 1.0 = 0.61
        assert score == pytest.approx(0.61, abs=0.01)

    def test_compute_score_empty(self) -> None:
        assert compute_score([]) == 0.0

    def test_write_reward(self, tmp_path: Path) -> None:
        vr = VerificationResult(
            task_id="test-001",
            checkpoint_results=[
                CheckpointResult(name="a", weight=1.0, passed=True, score=0.8),
            ],
            total_score=0.8,
        )
        reward_path = write_reward(vr, tmp_path / "reward.txt")
        assert reward_path.exists()
        content = reward_path.read_text()
        assert "test-001" in content
        assert "0.8000" in content


# ===========================================================================
# 4. Plugin validators (answer, code_patch, config)
# ===========================================================================


class TestPluginValidators:
    def test_plugin_registry_has_expected_types(self) -> None:
        validators = list_validators()
        assert "answer" in validators
        assert "code_patch" in validators
        assert "config" in validators

    def test_answer_validator_json(self, tmp_path: Path) -> None:
        workspace = tmp_path / "ws"
        workspace.mkdir()
        (workspace / "answer.json").write_text(json.dumps({"answer": "42"}))

        validator = get_validator("answer")
        assert validator is not None
        result = validator.validate(workspace)
        assert result.valid is True

    def test_answer_validator_txt(self, tmp_path: Path) -> None:
        workspace = tmp_path / "ws"
        workspace.mkdir()
        (workspace / "answer.txt").write_text("The answer is 42.\n")

        validator = get_validator("answer")
        assert validator is not None
        result = validator.validate(workspace)
        assert result.valid is True

    def test_answer_validator_missing(self, tmp_path: Path) -> None:
        workspace = tmp_path / "ws"
        workspace.mkdir()

        validator = get_validator("answer")
        assert validator is not None
        result = validator.validate(workspace)
        assert result.valid is False

    def test_config_validator_valid_json(self, tmp_path: Path) -> None:
        workspace = tmp_path / "ws"
        output = workspace / "output"
        output.mkdir(parents=True)
        (output / "config.json").write_text(json.dumps({"key": "value"}))

        validator = get_validator("config")
        assert validator is not None
        result = validator.validate(workspace)
        assert result.valid is True

    def test_config_validator_invalid_json(self, tmp_path: Path) -> None:
        workspace = tmp_path / "ws"
        output = workspace / "output"
        output.mkdir(parents=True)
        (output / "config.json").write_text("{invalid json")

        validator = get_validator("config")
        assert validator is not None
        result = validator.validate(workspace)
        assert result.valid is False

    def test_code_patch_validator_no_repos(self, tmp_path: Path) -> None:
        workspace = tmp_path / "ws"
        workspace.mkdir()

        validator = get_validator("code_patch")
        assert validator is not None
        result = validator.validate(workspace)
        assert result.valid is False


# ===========================================================================
# 5. Full end-to-end: parse → run_all → scored JSON output
# ===========================================================================


class TestEndToEnd:
    def test_full_pipeline(self, smoke_env: tuple[Path, Path, Path]) -> None:
        task_toml, task_dir, workspace = smoke_env
        task = parse_task(task_toml)
        runner = CheckpointRunner(task=task, task_dir=task_dir, workspace=workspace)

        reward_path = workspace / "reward.txt"
        verification = runner.run_all(output_path=reward_path)

        # Verify the result structure
        assert isinstance(verification, VerificationResult)
        assert verification.task_id == "smoke-test-001"
        assert len(verification.checkpoint_results) == 3

        # Check individual checkpoint results
        cp_map = {cr.name: cr for cr in verification.checkpoint_results}
        assert cp_map["always_pass"].passed is True
        assert cp_map["always_pass"].score == 1.0
        assert cp_map["partial_score"].passed is True
        assert cp_map["partial_score"].score == pytest.approx(0.6)
        assert cp_map["always_fail"].passed is False
        assert cp_map["always_fail"].score == 0.0

        # Expected total: (1.0*0.4 + 0.6*0.35 + 0.0*0.25) / 1.0 = 0.61
        assert verification.total_score == pytest.approx(0.61, abs=0.01)

        # Artifact validation ran
        assert len(verification.artifact_results) == 2  # answer + config
        art_map = {ar["type"]: ar for ar in verification.artifact_results}
        assert art_map["answer"]["valid"] is True
        assert art_map["config"]["valid"] is True

        # reward.txt was written
        assert reward_path.exists()
        content = reward_path.read_text()
        assert "smoke-test-001" in content
        assert "always_pass" in content

    def test_full_pipeline_json_serializable(
        self, smoke_env: tuple[Path, Path, Path]
    ) -> None:
        """Verify that the result can be serialized to JSON."""
        task_toml, task_dir, workspace = smoke_env
        task = parse_task(task_toml)
        runner = CheckpointRunner(task=task, task_dir=task_dir, workspace=workspace)

        verification = runner.run_all(output_path=workspace / "reward.txt")

        # Build JSON output matching what a harness would produce
        output = {
            "task_id": verification.task_id,
            "total_score": verification.total_score,
            "checkpoints": [
                {
                    "name": cr.name,
                    "weight": cr.weight,
                    "passed": cr.passed,
                    "score": cr.score,
                    "detail": cr.detail,
                }
                for cr in verification.checkpoint_results
            ],
            "artifacts": verification.artifact_results,
        }

        # Must be JSON-serializable
        json_str = json.dumps(output, indent=2)
        parsed = json.loads(json_str)

        assert parsed["task_id"] == "smoke-test-001"
        assert len(parsed["checkpoints"]) == 3
        assert isinstance(parsed["total_score"], float)
        assert 0.0 <= parsed["total_score"] <= 1.0

    def test_summary_output(self, smoke_env: tuple[Path, Path, Path]) -> None:
        """Verify that VerificationResult.summary() produces readable output."""
        task_toml, task_dir, workspace = smoke_env
        task = parse_task(task_toml)
        runner = CheckpointRunner(task=task, task_dir=task_dir, workspace=workspace)

        verification = runner.run_all(output_path=workspace / "reward.txt")
        summary = verification.summary()

        assert "smoke-test-001" in summary
        assert "PASS" in summary
        assert "FAIL" in summary
        assert "answer" in summary
        assert "config" in summary
