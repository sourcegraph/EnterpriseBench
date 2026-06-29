"""Test that CheckpointRunner warns when expected_solution.json is partial."""

from __future__ import annotations

import json
import logging
from pathlib import Path
from unittest.mock import patch

import pytest

from eb_verify.errors import VerifierInfraError
from eb_verify.runner import CheckpointRunner
from eb_verify.task_parser import (
    ArtifactSpec,
    Checkpoint,
    RepoSpec,
    TaskDefinition,
)


def _make_task(checkpoints: list[Checkpoint]) -> TaskDefinition:
    return TaskDefinition(
        id="warn-test-001",
        suite="incident_response",
        difficulty="hard",
        session_type="single",
        repos=[RepoSpec(url="http://x", rev="main", path="repo1")],
        checkpoints=checkpoints,
        artifacts=ArtifactSpec(),
        verification_modes=["llm_curator"],
    )


def test_unmapped_checkpoint_emits_warning(tmp_path: Path, caplog) -> None:
    """C1 visibility: a partial expected_solution.json should log a WARNING."""
    task_dir = tmp_path / "task"
    task_dir.mkdir()
    workspace = tmp_path / "ws"
    workspace.mkdir()
    (workspace / "repo1").mkdir()

    (task_dir / "expected_solution.json").write_text(
        json.dumps(
            {
                "task_id": "warn-test-001",
                "checkpoints": {
                    "root_cause": {
                        "expected_solution": "x",
                        "evaluation_criteria": ["a", "b"],
                    },
                    # "remediation" deliberately missing
                },
            }
        )
    )

    task = _make_task(
        [
            Checkpoint(name="root_cause", weight=0.5, verifier="x", timeout_seconds=60),
            Checkpoint(name="remediation", weight=0.5, verifier="x", timeout_seconds=60),
        ]
    )

    caplog.set_level(logging.WARNING, logger="eb_verify.runner")
    CheckpointRunner(task=task, task_dir=task_dir, workspace=workspace)

    msgs = [r.getMessage() for r in caplog.records]
    assert any(
        "missing checkpoint 'remediation'" in m and "grep_score" in m for m in msgs
    ), f"expected unmapped-checkpoint warning, got {msgs}"


def test_complete_expected_solution_emits_no_warning(tmp_path: Path, caplog) -> None:
    task_dir = tmp_path / "task"
    task_dir.mkdir()
    workspace = tmp_path / "ws"
    workspace.mkdir()
    (workspace / "repo1").mkdir()

    (task_dir / "expected_solution.json").write_text(
        json.dumps(
            {
                "task_id": "warn-test-001",
                "checkpoints": {
                    "root_cause": {
                        "expected_solution": "x",
                        "evaluation_criteria": ["a", "b"],
                    },
                    "remediation": {
                        "expected_solution": "y",
                        "evaluation_criteria": ["a", "b"],
                    },
                },
            }
        )
    )

    task = _make_task(
        [
            Checkpoint(name="root_cause", weight=0.5, verifier="x", timeout_seconds=60),
            Checkpoint(name="remediation", weight=0.5, verifier="x", timeout_seconds=60),
        ]
    )

    caplog.set_level(logging.WARNING, logger="eb_verify.runner")
    CheckpointRunner(task=task, task_dir=task_dir, workspace=workspace)
    msgs = [r.getMessage() for r in caplog.records]
    assert not any(
        "missing checkpoint" in m for m in msgs
    ), f"expected no warning, got {msgs}"


def test_no_warning_when_expected_solution_absent(tmp_path: Path, caplog) -> None:
    task_dir = tmp_path / "task"
    task_dir.mkdir()
    workspace = tmp_path / "ws"
    workspace.mkdir()
    (workspace / "repo1").mkdir()

    task = _make_task(
        [
            Checkpoint(name="root_cause", weight=0.5, verifier="x", timeout_seconds=60),
        ]
    )

    caplog.set_level(logging.WARNING, logger="eb_verify.runner")
    CheckpointRunner(task=task, task_dir=task_dir, workspace=workspace)
    msgs = [r.getMessage() for r in caplog.records]
    assert not any("missing checkpoint" in m for m in msgs)


def test_judge_init_failure_raises_infra_error(tmp_path: Path) -> None:
    """F4.3: llm_curator + expected_solution but the Tier-2 judge fails to
    construct must raise VerifierInfraError, not silently degrade to judge=None
    (which would pass every checkpoint at its un-capped grep score)."""
    task_dir = tmp_path / "task"
    task_dir.mkdir()
    workspace = tmp_path / "ws"
    workspace.mkdir()
    (workspace / "repo1").mkdir()

    (task_dir / "expected_solution.json").write_text(
        json.dumps(
            {
                "task_id": "warn-test-001",
                "checkpoints": {
                    "root_cause": {
                        "expected_solution": "x",
                        "evaluation_criteria": ["a"],
                    }
                },
            }
        )
    )

    task = _make_task(
        [Checkpoint(name="root_cause", weight=1.0, verifier="x", timeout_seconds=60)]
    )

    with patch("eb_verify.judge.LLMJudge", side_effect=RuntimeError("no API key")):
        with pytest.raises(VerifierInfraError) as exc:
            CheckpointRunner(task=task, task_dir=task_dir, workspace=workspace)

    assert exc.value.reason == "judge_init_failed"
