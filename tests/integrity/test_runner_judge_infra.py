"""Judge failure on the Python runner path (CheckpointRunner) — bead fi9mm.

The runner is the third scoring entry point's *other* consumer: it applies the
same min(grep, judge) cap as run_task. When the judge reaches no verdict the cap
cannot be applied, and neither number available to the runner is a measurement —
the un-capped grep score over-credits, and a 0.0 blames the agent for our outage.

The runner must therefore declare the infra failure in the checkpoint detail, so
scorer_guard routes the run to the re-run channel instead of scoring it.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path
from unittest.mock import MagicMock

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(REPO_ROOT / "lib"))

from eb_verify.judge import JudgeBackendError  # noqa: E402
from eb_verify.judge.models import JudgeScoreError  # noqa: E402
from eb_verify.runner import CheckpointRunner  # noqa: E402
from eb_verify.scorer_guard import (  # noqa: E402
    INFRA_SENTINEL,
    InfraError,
    guard_verifier_output,
)
from eb_verify.task_parser import (  # noqa: E402
    ArtifactSpec,
    Checkpoint,
    RepoSpec,
    TaskDefinition,
)


def _runner_with_judge(
    tmp_path: Path,
    judge_error: Exception | None = None,
    checkpoint_names: tuple[str, ...] = ("root_cause",),
) -> CheckpointRunner:
    """A runner whose grep verifier scores 1.0 on every checkpoint.

    With ``judge_error`` the judge always raises it; without, the caller wires
    ``runner._judge.evaluate_checkpoint.return_value`` itself.
    """
    task_dir = tmp_path / "task"
    task_dir.mkdir()
    workspace = tmp_path / "ws"
    (workspace / "repo1").mkdir(parents=True)
    (workspace / "agent_output").mkdir()
    (workspace / "agent_output" / "answer.json").write_text('{"answer": "X"}')

    (task_dir / "expected_solution.json").write_text(
        json.dumps(
            {
                "task_id": "fi9mm-001",
                "checkpoints": {
                    name: {
                        "expected_solution": "the answer is X",
                        "evaluation_criteria": ["mentions X"],
                    }
                    for name in checkpoint_names
                },
            }
        )
    )

    verifier = task_dir / "check.sh"
    verifier.write_text('#!/usr/bin/env bash\necho \'{"score": 1.0, "detail": "grep hit"}\'\n')
    verifier.chmod(0o755)

    task = TaskDefinition(
        id="fi9mm-001",
        suite="incident_response",
        difficulty="hard",
        session_type="single",
        repos=[RepoSpec(url="http://x", rev="main", path="repo1")],
        checkpoints=[
            Checkpoint(
                name=name, weight=1.0, verifier="check.sh", timeout_seconds=60
            )
            for name in checkpoint_names
        ],
        artifacts=ArtifactSpec(),
        verification_modes=["llm_curator"],
    )

    runner = CheckpointRunner(task=task, task_dir=task_dir, workspace=workspace)
    judge = MagicMock()
    if judge_error is not None:
        judge.evaluate_checkpoint.side_effect = judge_error
    runner._judge = judge
    return runner


@pytest.mark.parametrize(
    "judge_error",
    [
        JudgeBackendError("judge 503"),
        JudgeScoreError("judge returned a non-score: nan"),
    ],
)
def test_judge_failure_declares_infra_and_routes_to_rerun(
    tmp_path: Path, judge_error: Exception
) -> None:
    runner = _runner_with_judge(tmp_path, judge_error)
    result = runner.run_all(output_path=tmp_path / "reward.txt")

    (cp,) = result.checkpoint_results
    assert INFRA_SENTINEL in cp.detail, (
        "a judge that reached no verdict must be declared, not scored — the "
        "un-capped grep 1.0 would otherwise stand as the final measurement"
    )

    # The declaration is not decoration: scorer_guard must route on it.
    # verifier_ran=true is set so the checkpoint clears the PRIMARY attestation
    # gate (bead glka.2) and reaches the SECONDARY detail-signature net this test
    # is about: a verifier that reached a verdict yet declared its own harness
    # failure in the detail must still route as verifier_crash, not be scored.
    stdout = json.dumps(
        {
            "task_score": result.total_score,
            "checkpoints": [
                {
                    "name": cp.name,
                    "score": cp.score,
                    "detail": cp.detail,
                    "verifier_ran": True,
                }
            ],
        }
    )
    guarded = guard_verifier_output(stdout, returncode=1)
    assert isinstance(guarded, InfraError)
    assert guarded.reason == "verifier_crash"


def test_a_dead_judge_is_not_called_again_for_later_checkpoints(tmp_path: Path) -> None:
    """The first no-verdict already routes the run to re-run, and an outage fails
    every later call anyway — at a 120s timeout plus retries each. Later
    checkpoints must still declare infra, without paying for the call."""
    runner = _runner_with_judge(
        tmp_path,
        JudgeBackendError("judge 503"),
        checkpoint_names=("root_cause", "blast_radius", "remediation"),
    )

    result = runner.run_all(output_path=tmp_path / "reward.txt")

    assert runner._judge.evaluate_checkpoint.call_count == 1, (
        "the judge was already known to be down; re-calling it burns a network "
        "timeout per checkpoint for a number the re-run channel discards"
    )
    assert len(result.checkpoint_results) == 3
    for cp in result.checkpoint_results:
        assert INFRA_SENTINEL in cp.detail, (
            f"{cp.name}: skipping the judge call must not skip the declaration — "
            "the un-capped grep 1.0 would otherwise stand"
        )


def test_healthy_judge_still_caps_without_declaring_infra(tmp_path: Path) -> None:
    """Negative control: a judge that returns a verdict caps grep as before."""
    runner = _runner_with_judge(tmp_path)
    runner._judge.evaluate_checkpoint.return_value = MagicMock(
        score=0.25, confidence="high", reasoning="partial"
    )

    result = runner.run_all(output_path=tmp_path / "reward.txt")

    (cp,) = result.checkpoint_results
    assert INFRA_SENTINEL not in cp.detail
    assert cp.score == 0.25  # min(grep 1.0, judge 0.25)
