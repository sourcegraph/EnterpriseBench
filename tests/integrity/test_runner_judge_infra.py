"""Judge failure on the Python runner path (CheckpointRunner) — beads fi9mm, yli6k.

The runner is the third scoring entry point's *other* consumer: it applies the
same min(grep, judge) cap as run_task. When the judge reaches no verdict the cap
cannot be applied, and neither number available to the runner is a measurement —
the un-capped grep score over-credits, and a 0.0 blames the agent for our outage.

The runner must therefore emit a TYPED infra failure (``CheckpointResult`` with
an ``infra_error``), which ``run_all`` collects into ``verifier_infra_error`` so
the run routes to the re-run channel and cli.py exits 2. yli6k replaced fi9mm's
inert string sentinel: nothing re-parsed the runner's own composed detail, so the
placeholder 0.0 stood as a real score, and the failure carried a different reason
key ("verifier_crash") than run_task's ("judge_checkpoint_failed").
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
    # A judge that reached no verdict is a TYPED infra failure, not a score — the
    # un-capped grep 1.0 must not stand as the final measurement.
    assert cp.infra_error is not None, (
        "the judge no-verdict must surface as a typed infra_error, not a "
        "sentinel string the runner's own detail composes and nobody re-parses"
    )
    assert cp.infra_error.reason == "judge_checkpoint_failed"

    # The typed error is what routes the run: run_all lifts it to
    # verifier_infra_error (the field cli.py reads to exit 2) and refuses a
    # numeric total. Both carry the ONE reason key run_task also uses.
    assert result.verifier_infra_error is not None
    assert result.verifier_infra_error["reason"] == "judge_checkpoint_failed"
    assert result.verifier_infra_error["checkpoint"] == cp.name
    assert result.total_score == 0.0


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
        assert cp.infra_error is not None, (
            f"{cp.name}: skipping the judge call must not skip the declaration — "
            "the un-capped grep 1.0 would otherwise stand"
        )
        assert cp.infra_error.reason == "judge_checkpoint_failed"
    assert result.verifier_infra_error is not None


def test_healthy_judge_still_caps_without_declaring_infra(tmp_path: Path) -> None:
    """Negative control: a judge that returns a verdict caps grep as before."""
    runner = _runner_with_judge(tmp_path)
    runner._judge.evaluate_checkpoint.return_value = MagicMock(
        score=0.25, confidence="high", reasoning="partial"
    )

    result = runner.run_all(output_path=tmp_path / "reward.txt")

    (cp,) = result.checkpoint_results
    assert cp.infra_error is None
    assert result.verifier_infra_error is None
    assert cp.score == 0.25  # min(grep 1.0, judge 0.25)


def test_non_judge_exception_propagates_not_laundered_to_infra(tmp_path: Path) -> None:
    """yli6k #3: only a JudgeError is an infra no-verdict. A bare RuntimeError
    (stand-in for an AttributeError/TypeError from a future refactor) must
    propagate as the real bug it is, not be swallowed into a re-run loop."""
    runner = _runner_with_judge(tmp_path, RuntimeError("bug in judge glue"))

    with pytest.raises(RuntimeError, match="bug in judge glue"):
        runner.run_all(output_path=tmp_path / "reward.txt")
