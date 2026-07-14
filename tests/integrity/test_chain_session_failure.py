"""A chain whose sessions did not all complete has no score.

The same over-credit as bead chc2z, one level up: chc2z closed fabrication
*inside* the verifier (an exit code is not a score), this closes it *around* the
verifier. A failed session broke out of the session loop, and the final
checkpoints then ran anyway against a workspace the agent never worked in. They
score whatever is on disk, so they reached real verdicts and nothing downstream
looked broken:

    Session 1: FAIL (list index out of range) -> N/A
    Session 2 milestones: 1.00
    Total score: 1.00
    exit 0  ->  run_benchmark records status="completed"

A failed session is not a verifier failure, so it reports through its own
``session_failure`` channel: same re-run destination, honest cause.
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(REPO_ROOT / "scripts"))
sys.path.insert(0, str(REPO_ROOT / "lib"))

from eb_verify.scorer_guard import NO_VERDICT_REASON
from orchestration.chain_runner import (
    SESSION_FAILURE_REASON,
    ChainTaskDefinition,
    run_chain,
)
from orchestration.session import SessionConfig


def write_verifier(task_dir: Path, name: str, body: str) -> str:
    """Write an executable verifier under ``task_dir/checks``; return its
    task-relative path (the form a task.toml carries).

    Same name and signature as ``test_checkpoint_verdict.write_verifier`` so that a
    call copied between the two modules keeps working.
    """
    checks = task_dir / "checks"
    checks.mkdir(exist_ok=True)
    script = checks / name
    script.write_text(body)
    script.chmod(0o755)
    return f"checks/{name}"


def scoring_verifier(task_dir: Path, score: float, name: str = "final.sh") -> str:
    """A verifier that always scores, and leaves a sentinel proving it ran.

    Asserting only on the resulting score would also pass for a fix that merely
    zeroed it; these tests have to show the checkpoints never executed at all.
    """
    return write_verifier(
        task_dir,
        name,
        f'#!/bin/sh\ntouch "{task_dir}/{name}.ran"\necho \'{{"score": {score}}}\'\nexit 0\n',
    )


def verifier_ran(task_dir: Path, name: str = "final.sh") -> bool:
    return (task_dir / f"{name}.ran").exists()


def task_def(
    session_count: int,
    final_checkpoints: list[dict] | None = None,
    session_milestones: dict[int, list[dict]] | None = None,
) -> ChainTaskDefinition:
    milestones = session_milestones or {}
    return ChainTaskDefinition(
        task_id="chain-session-failure",
        suite="customer_escalation",
        difficulty="medium",
        session_count=session_count,
        repos=[{"path": "repo-a"}],
        sessions=[
            SessionConfig(
                session_number=n,
                prompt=f"session {n}",
                milestones=milestones.get(n, []),
            )
            for n in range(1, session_count + 1)
        ],
        final_checkpoints=final_checkpoints or [],
    )


def agent_failing_at(session_number: int | None):
    """An agent that works normally until ``session_number``, then dies.

    ``None`` never fails — the healthy agent the negative controls need.
    """
    calls = {"n": 0}

    def agent(workspace: str, prompt: str) -> str:
        calls["n"] += 1
        if calls["n"] == session_number:
            raise RuntimeError("agent died")
        (Path(workspace) / "repo-a" / f"session_{calls['n']}.md").write_text("work\n")
        return f"session {calls['n']} done"

    return agent


def working_agent():
    return agent_failing_at(None)


class TestFailedSessionIsNeverAScore:
    def test_failed_first_session_scores_nothing_and_never_runs_the_checkpoints(
        self, tmp_path
    ):
        """THE BUG. Two sessions configured, the agent dies in session 1. The
        final checkpoints used to run anyway and hand the chain a perfect 1.0."""
        verifier = scoring_verifier(tmp_path, 1.0)
        td = task_def(
            session_count=2,
            final_checkpoints=[{"name": "cp", "weight": 1.0, "verifier": verifier}],
        )

        result = run_chain(
            td,
            workspace_root=str(tmp_path / "ws"),
            agent_callable=agent_failing_at(1),
            task_dir=str(tmp_path),
        )

        assert result.session_failure is not None
        assert result.session_failure["reason"] == SESSION_FAILURE_REASON
        assert result.session_failure["session_number"] == 1
        assert result.total_score is None, "a chain that ran no session has no score"
        assert result.final_score is None
        assert not verifier_ran(tmp_path), (
            "final checkpoints must not verify a workspace the agent never worked in"
        )

    def test_mid_chain_failure_does_not_score_from_the_earlier_milestones(
        self, tmp_path
    ):
        """Gating the final checkpoints is not enough on its own: session 1's
        inter-session milestone already scored 1.0 and sits in milestone_scores,
        so the weighted sum would still produce a legitimate-looking total for a
        chain that died at session 2."""
        milestone = scoring_verifier(tmp_path, 1.0, name="m1.sh")
        td = task_def(
            session_count=2,
            session_milestones={1: [{"name": "m1", "verifier": milestone}]},
        )

        result = run_chain(
            td,
            workspace_root=str(tmp_path / "ws"),
            agent_callable=agent_failing_at(2),
            task_dir=str(tmp_path),
        )

        assert verifier_ran(tmp_path, "m1.sh"), "session 1's milestone did run"
        assert result.milestone_scores[0].total_score == pytest.approx(1.0)
        assert result.session_failure["session_number"] == 2
        assert result.total_score is None, "one passing milestone is not a chain score"

    def test_a_broken_verifier_and_a_failed_session_are_both_reported(self, tmp_path):
        """An operator needs both signals: fixing the verifier would not make this
        run scoreable, and re-running the agent would not fix the verifier."""
        broken = write_verifier(tmp_path, "broken.sh", "#!/bin/sh\nexit 0\n")
        td = task_def(
            session_count=2,
            session_milestones={1: [{"name": "m1", "verifier": broken}]},
        )

        result = run_chain(
            td,
            workspace_root=str(tmp_path / "ws"),
            agent_callable=agent_failing_at(2),
            task_dir=str(tmp_path),
        )

        assert result.session_failure is not None
        assert result.verifier_infra_error is not None
        assert result.verifier_infra_error["reason"] == NO_VERDICT_REASON
        assert result.total_score is None

    def test_summary_renders_invalid_run_instead_of_a_number(self, tmp_path):
        td = task_def(
            session_count=2,
            final_checkpoints=[
                {"name": "cp", "weight": 1.0, "verifier": scoring_verifier(tmp_path, 1.0)}
            ],
        )

        text = run_chain(
            td,
            workspace_root=str(tmp_path / "ws"),
            agent_callable=agent_failing_at(1),
            task_dir=str(tmp_path),
        ).summary()

        assert "INVALID RUN" in text
        assert "0.00" not in text, "an unscoreable run must not render as a zero"
        assert "1.00" not in text, "...nor as the fabricated perfect score"


class TestTheGateCannotBeSilentlyDefeated:
    """The final-checkpoint gate counts completed sessions against session_count,
    so session_count has to BE the session count. Both ways of breaking that are
    rejected at the entry point rather than silently mis-scoring."""

    def test_a_zero_session_chain_is_rejected(self, tmp_path):
        """0 completed == 0 configured satisfies the gate VACUOUSLY, so the final
        checkpoints would run and score an empty workspace no agent ever touched —
        the same fabrication, reached through a degenerate config instead of a
        failed session."""
        td = task_def(
            session_count=0,
            final_checkpoints=[
                {"name": "cp", "weight": 1.0, "verifier": scoring_verifier(tmp_path, 1.0)}
            ],
        )

        with pytest.raises(ValueError, match="no sessions"):
            run_chain(td, workspace_root=str(tmp_path / "ws"), task_dir=str(tmp_path))

        assert not verifier_ran(tmp_path)

    def test_a_task_def_with_fewer_sessions_than_session_count_is_rejected(
        self, tmp_path
    ):
        """Otherwise every session passes, the gate still skips the checkpoints, and
        the chain exits 0 with no score — recorded as a completed run. Reachable
        without the parser: ChainTaskDefinition is a mutable dataclass, and callers
        (including tests in this repo) build one directly."""
        td = task_def(session_count=2)
        td.sessions = td.sessions[:1]

        with pytest.raises(ValueError, match="session_count=2 but 1 session"):
            run_chain(
                td,
                workspace_root=str(tmp_path / "ws"),
                agent_callable=working_agent(),
                task_dir=str(tmp_path),
            )


class TestHealthyChainStillScores:
    def test_every_session_completing_scores_the_final_checkpoints(self, tmp_path):
        """The fix must not stop a real chain from scoring."""
        td = task_def(
            session_count=2,
            final_checkpoints=[
                {"name": "cp", "weight": 1.0, "verifier": scoring_verifier(tmp_path, 0.5)}
            ],
        )

        result = run_chain(
            td,
            workspace_root=str(tmp_path / "ws"),
            agent_callable=working_agent(),
            task_dir=str(tmp_path),
        )

        assert not result.is_invalid
        assert result.session_failure is None
        assert verifier_ran(tmp_path)
        assert result.total_score == pytest.approx(0.5)


class TestSessionFailureJsonContract:
    """End-to-end through the CLI: the file a downstream reader parses, and the
    exit code ``run_benchmark`` routes on."""

    def test_json_carries_no_score_and_the_exit_is_nonzero(self, tmp_path):
        task_toml = tmp_path / "task.toml"
        # repos = [] is the bead's repro vehicle: simulate_agent_work indexes
        # repos[0] and raises IndexError, so session 1 fails for a reason that has
        # nothing to do with the verifier.
        task_toml.write_text(
            '[task]\n'
            'id = "chain-session-failure"\n'
            'suite = "customer_escalation"\n'
            'difficulty = "medium"\n'
            'session_type = "chain"\n'
            'session_count = 2\n'
            '\n'
            '[[sessions]]\n'
            'prompt = "session 1"\n'
            '\n'
            '[[sessions]]\n'
            'prompt = "session 2"\n'
            '\n'
            '[[checkpoints]]\n'
            'name = "cp"\n'
            'weight = 1.0\n'
            f'verifier = "{scoring_verifier(tmp_path, 1.0)}"\n'
        )

        proc = subprocess.run(
            [sys.executable, "-m", "orchestration.chain_runner", str(task_toml), "--simulate"],
            capture_output=True,
            text=True,
            cwd=str(REPO_ROOT / "scripts"),
            timeout=120,
        )

        assert proc.returncode != 0, "a chain that never completed must not exit 0"

        payload = json.loads((tmp_path / "chain_result.json").read_text())
        assert payload["total_score"] is None, "no number for a chain that never ran"
        assert payload["sessions_completed"] == 0
        # Both invalidity channels are built from InfraError, so a reader of
        # chain_result.json parses either one the same way — `cause` included.
        assert payload["session_failure"]["reason"] == SESSION_FAILURE_REASON
        assert payload["session_failure"]["cause"] == SESSION_FAILURE_REASON
        assert payload["session_failure"]["stage"] == "session"
        assert payload["session_failure"]["session_number"] == 1
        assert payload["session_failure"]["detail"]
        assert payload["sessions_total"] == 2, (
            "sessions_total must report the CONFIGURED session count — reporting "
            "len(session_results) hid the missing sessions behind 0/1"
        )
        assert not verifier_ran(tmp_path)
