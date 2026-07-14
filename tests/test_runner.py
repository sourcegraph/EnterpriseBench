"""Tests for eb_verify.runner."""

from __future__ import annotations

import json
import os
import stat
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from eb_verify.runner import CheckpointRunner
from eb_verify.scoring import CheckpointResult
from eb_verify.task_parser import (
    ArtifactSpec,
    Checkpoint,
    RepoSpec,
    TaskDefinition,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def make_task(
    task_id: str = "test-001",
    repos: list | None = None,
    checkpoints: list | None = None,
    artifacts: ArtifactSpec | None = None,
) -> TaskDefinition:
    return TaskDefinition(
        id=task_id,
        suite="dependency_management",
        difficulty="medium",
        session_type="single",
        repos=repos or [],
        checkpoints=checkpoints or [],
        artifacts=artifacts or ArtifactSpec(),
    )


def make_checkpoint(
    name: str = "cp1",
    weight: float = 1.0,
    verifier: str = "checks/check.sh",
    timeout_seconds: int = 120,
) -> Checkpoint:
    return Checkpoint(name=name, weight=weight, verifier=verifier, timeout_seconds=timeout_seconds)


def write_bash_script(path: Path, content: str) -> Path:
    """Write a bash script and make it executable."""
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content)
    path.chmod(path.stat().st_mode | stat.S_IEXEC | stat.S_IXGRP | stat.S_IXOTH)
    return path


# ---------------------------------------------------------------------------
# sandbox_health_check
# ---------------------------------------------------------------------------

class TestSandboxHealthCheck:
    def test_passes_when_all_repos_exist(self, tmp_path):
        workspace = tmp_path / "ws"
        workspace.mkdir()
        (workspace / "repo1").mkdir()
        (workspace / "repo2").mkdir()

        repos = [
            RepoSpec(url="http://x", rev="main", path="repo1"),
            RepoSpec(url="http://y", rev="main", path="repo2"),
        ]
        task = make_task(repos=repos)
        runner = CheckpointRunner(task=task, workspace=workspace)
        assert runner.sandbox_health_check() is True

    def test_fails_when_repo_missing(self, tmp_path):
        workspace = tmp_path / "ws"
        workspace.mkdir()
        (workspace / "repo1").mkdir()
        # repo2 intentionally absent

        repos = [
            RepoSpec(url="http://x", rev="main", path="repo1"),
            RepoSpec(url="http://y", rev="main", path="repo2"),
        ]
        task = make_task(repos=repos)
        runner = CheckpointRunner(task=task, workspace=workspace)
        assert runner.sandbox_health_check() is False

    def test_passes_with_no_repos(self, tmp_path):
        task = make_task(repos=[])
        runner = CheckpointRunner(task=task, workspace=tmp_path)
        assert runner.sandbox_health_check() is True


# ---------------------------------------------------------------------------
# run_checkpoint — verifier not found
# ---------------------------------------------------------------------------

class TestRunCheckpointMissingVerifier:
    def test_missing_verifier_returns_fail(self, tmp_path):
        task_dir = tmp_path / "task"
        task_dir.mkdir()
        workspace = tmp_path / "ws"
        workspace.mkdir()

        task = make_task()
        cp = make_checkpoint(verifier="checks/nonexistent.sh")
        runner = CheckpointRunner(task=task, task_dir=task_dir, workspace=workspace)
        result = runner.run_checkpoint(cp)

        # A verifier that isn't there never judged the agent — that's an infra
        # failure, not a 0.0 the agent earned (bead kyo34).
        assert result.infra_error is not None
        assert result.infra_error.context["cause"] == "missing_verifier"
        assert result.passed is False
        assert "not found" in result.detail.lower()


# ---------------------------------------------------------------------------
# run_checkpoint — JSON output parsing
# ---------------------------------------------------------------------------

class TestRunCheckpointJsonOutput:
    def test_json_score_1(self, tmp_path):
        task_dir = tmp_path / "task"
        task_dir.mkdir()
        workspace = tmp_path / "ws"
        workspace.mkdir()
        script = write_bash_script(
            task_dir / "checks" / "pass.sh",
            '#!/bin/bash\necho \'{"score": 1.0, "detail": "all good"}\'\n',
        )

        task = make_task()
        cp = make_checkpoint(verifier="checks/pass.sh")
        runner = CheckpointRunner(task=task, task_dir=task_dir, workspace=workspace)
        result = runner.run_checkpoint(cp)

        assert result.passed is True
        assert result.score == pytest.approx(1.0)
        assert result.detail == "all good"

    def test_json_score_0(self, tmp_path):
        task_dir = tmp_path / "task"
        task_dir.mkdir()
        workspace = tmp_path / "ws"
        workspace.mkdir()
        write_bash_script(
            task_dir / "checks" / "fail.sh",
            '#!/bin/bash\necho \'{"score": 0.0, "detail": "nothing done"}\'\n',
        )

        task = make_task()
        cp = make_checkpoint(verifier="checks/fail.sh")
        runner = CheckpointRunner(task=task, task_dir=task_dir, workspace=workspace)
        result = runner.run_checkpoint(cp)

        assert result.passed is False
        assert result.score == pytest.approx(0.0)

    def test_json_partial_score(self, tmp_path):
        task_dir = tmp_path / "task"
        task_dir.mkdir()
        workspace = tmp_path / "ws"
        workspace.mkdir()
        write_bash_script(
            task_dir / "checks" / "partial.sh",
            '#!/bin/bash\necho \'{"score": 0.6, "detail": "partial"}\'\n',
        )

        task = make_task()
        cp = make_checkpoint(verifier="checks/partial.sh")
        runner = CheckpointRunner(task=task, task_dir=task_dir, workspace=workspace)
        result = runner.run_checkpoint(cp)

        assert result.passed is True  # score > 0
        assert result.score == pytest.approx(0.6)


# ---------------------------------------------------------------------------
# run_checkpoint — there is NO exit-code fallback (bead kyo34)
#
# These two tests previously asserted the fallback: `exit 0` scored 1.0 and
# `exit 1` scored 0.0 for a verifier that emitted no JSON. That WAS the bug —
# it handed full credit to a verifier that crashed before scoring anything, and
# blamed the agent for a broken harness. Full no-verdict coverage lives in
# tests/integrity/test_checkpoint_verdict.py.
# ---------------------------------------------------------------------------

class TestRunCheckpointNoExitCodeFallback:
    def test_exit_0_without_json_is_infra_not_a_pass(self, tmp_path):
        task_dir = tmp_path / "task"
        task_dir.mkdir()
        workspace = tmp_path / "ws"
        workspace.mkdir()
        write_bash_script(
            task_dir / "checks" / "ok.sh",
            "#!/bin/bash\nexit 0\n",
        )

        task = make_task()
        cp = make_checkpoint(verifier="checks/ok.sh")
        runner = CheckpointRunner(task=task, task_dir=task_dir, workspace=workspace)
        result = runner.run_checkpoint(cp)

        assert result.infra_error is not None
        assert result.passed is False
        assert result.score != pytest.approx(1.0)

    def test_exit_1_without_json_is_infra_not_a_real_zero(self, tmp_path):
        task_dir = tmp_path / "task"
        task_dir.mkdir()
        workspace = tmp_path / "ws"
        workspace.mkdir()
        write_bash_script(
            task_dir / "checks" / "nok.sh",
            "#!/bin/bash\nexit 1\n",
        )

        task = make_task()
        cp = make_checkpoint(verifier="checks/nok.sh")
        runner = CheckpointRunner(task=task, task_dir=task_dir, workspace=workspace)
        result = runner.run_checkpoint(cp)

        assert result.infra_error is not None
        assert result.passed is False


# ---------------------------------------------------------------------------
# run_checkpoint — timeout
# ---------------------------------------------------------------------------

class TestRunCheckpointTimeout:
    def test_timeout_returns_fail(self, tmp_path):
        task_dir = tmp_path / "task"
        task_dir.mkdir()
        workspace = tmp_path / "ws"
        workspace.mkdir()
        write_bash_script(
            task_dir / "checks" / "slow.sh",
            "#!/bin/bash\nsleep 200\n",
        )

        task = make_task()
        cp = Checkpoint(name="slow", weight=1.0, verifier="checks/slow.sh", timeout_seconds=120)

        runner = CheckpointRunner(task=task, task_dir=task_dir, workspace=workspace)

        # Mock subprocess.run to raise TimeoutExpired
        import subprocess
        with patch("eb_verify.runner.subprocess.run") as mock_run:
            mock_run.side_effect = subprocess.TimeoutExpired(cmd=["bash"], timeout=120)
            result = runner.run_checkpoint(cp)

        assert result.passed is False
        assert result.score == 0.0
        assert "timed out" in result.detail.lower()


# ---------------------------------------------------------------------------
# run_single
# ---------------------------------------------------------------------------

class TestRunSingle:
    def test_run_single_found(self, tmp_path):
        task_dir = tmp_path / "task"
        task_dir.mkdir()
        workspace = tmp_path / "ws"
        workspace.mkdir()
        write_bash_script(
            task_dir / "checks" / "cp.sh",
            '#!/bin/bash\necho \'{"score": 1.0, "detail": "ok"}\'\n',
        )

        cps = [
            make_checkpoint("alpha", 0.5, "checks/cp.sh"),
            make_checkpoint("beta", 0.5, "checks/cp.sh"),
        ]
        task = make_task(checkpoints=cps)
        runner = CheckpointRunner(task=task, task_dir=task_dir, workspace=workspace)
        result = runner.run_single("alpha")
        assert result.name == "alpha"

    def test_run_single_not_found(self, tmp_path):
        task = make_task(checkpoints=[make_checkpoint("only")])
        runner = CheckpointRunner(task=task, workspace=tmp_path)
        with pytest.raises(ValueError, match="Checkpoint not found"):
            runner.run_single("missing_name")


# ---------------------------------------------------------------------------
# validate_artifacts
# ---------------------------------------------------------------------------

class TestValidateArtifacts:
    def test_unknown_artifact_type(self, tmp_path):
        task = make_task(artifacts=ArtifactSpec(required=["unknown_type_xyz"]))
        runner = CheckpointRunner(task=task, workspace=tmp_path)
        results = runner.validate_artifacts()
        assert len(results) == 1
        assert results[0]["valid"] is False
        assert "No validator" in results[0]["detail"]

    def test_no_artifacts(self, tmp_path):
        task = make_task(artifacts=ArtifactSpec(required=[]))
        runner = CheckpointRunner(task=task, workspace=tmp_path)
        results = runner.validate_artifacts()
        assert results == []


# ---------------------------------------------------------------------------
# run_all integration
# ---------------------------------------------------------------------------

class TestRunAll:
    def test_run_all_writes_reward(self, tmp_path):
        task_dir = tmp_path / "task"
        task_dir.mkdir()
        workspace = tmp_path / "ws"
        workspace.mkdir()
        write_bash_script(
            task_dir / "checks" / "ok.sh",
            '#!/bin/bash\necho \'{"score": 1.0, "detail": "pass"}\'\n',
        )

        task = make_task(
            checkpoints=[make_checkpoint("step", 1.0, "checks/ok.sh")],
        )
        out = tmp_path / "reward.txt"
        runner = CheckpointRunner(task=task, task_dir=task_dir, workspace=workspace)
        result = runner.run_all(output_path=out)

        assert out.exists()
        assert result.total_score == pytest.approx(1.0)
        assert result.task_id == "test-001"
