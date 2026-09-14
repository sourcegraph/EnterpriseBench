"""A session that errored never reports success.

``run_session`` set ``result.success = True`` and only then dereferenced
``branch_state`` in a log line. When the commit loop produced no branch state the
dereference raised, the ``except`` recorded the error, and ``success`` stayed
True — a SessionResult carrying an error and no branch state, claiming it
completed:

    success      = True
    error        = "'NoneType' object has no attribute 'branch_name'"
    branch_state = None

That is the gate input 6c9wp added: ``sessions_completed`` counts ``sr.success``,
and the final checkpoints run only when every session completed. A session that
reports success while carrying an error walks the checkpoints onto a workspace
with no committed branch state, where they score whatever is on disk — the same
over-credit 6c9wp closed for the *failed*-session path, reached through a session
that merely *claims* it did not fail.

Latent, not active, when found: the only production path passes
``agent_callable=None`` (bl4bc), so ``repos=[]`` dies earlier with success=False,
and every real chain task has ``[[repos]]``. Fixed anyway, because the flag's
value as a gate input is exactly its trustworthiness.

Fail closed by construction: ``success`` is derived, not assigned — a session
succeeded exactly when it committed branch state and recorded no error. The
ordering bug is then unwritable rather than fixed, and chain_runner's gate gets
``not sr.error`` for free.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(REPO_ROOT / "scripts"))
sys.path.insert(0, str(REPO_ROOT / "lib"))

from orchestration.branch_manager import BranchState
from orchestration.chain_runner import ChainResult
from orchestration.session import SessionConfig, SessionResult, run_session


def working_agent(output: str = "did work"):
    """An agent that returns cleanly and leaves the workspace alone."""
    return lambda workspace, prompt: output


def branch_state(session_number: int = 1) -> BranchState:
    return BranchState(
        branch_name=f"eb-chain-probe-session-{session_number}",
        commit_sha="deadbeefcafe",
        repo_path="/workspace/repo-a",
        session_number=session_number,
    )


class TestAnErroredSessionNeverReportsSuccess:
    def test_a_session_that_commits_no_branch_state_reports_failure(self, tmp_path):
        """THE BUG, as reproduced on the bead: an agent that returns cleanly with
        no repos to commit into left success=True beside a real error."""
        result = run_session(
            session_config=SessionConfig(session_number=1, prompt="p"),
            repos=[],
            task_id="probe",
            workspace_root=str(tmp_path / "ws"),
            agent_callable=working_agent(),
        )

        assert result.success is False, (
            "a session that committed no branch state has not completed"
        )
        assert result.branch_state is None
        assert result.error, "a failed session must say why it failed"

    def test_the_failure_names_the_missing_branch_state_not_a_nonetype_attribute(
        self, tmp_path
    ):
        """The error is the chain's ``session_failure`` detail and the operator's
        only clue. ``'NoneType' object has no attribute 'branch_name'`` is the
        log line's accident, not the cause."""
        result = run_session(
            session_config=SessionConfig(session_number=1, prompt="p"),
            repos=[],
            task_id="probe",
            workspace_root=str(tmp_path / "ws"),
            agent_callable=working_agent(),
        )

        assert "NoneType" not in result.error
        assert "branch state" in result.error.lower()

    def test_success_is_derived_so_it_cannot_be_set_beside_an_error(self):
        """The ordering bug in one assertion: the two fields cannot disagree,
        whatever order a future edit writes them in."""
        errored = SessionResult(
            session_number=1, branch_state=branch_state(), error="agent died"
        )
        assert errored.success is False

        with pytest.raises(TypeError):
            SessionResult(session_number=1, success=True)

    def test_the_chain_completion_gate_does_not_count_an_errored_session(self):
        """``sessions_completed == session_count`` is what admits the final
        checkpoints. An errored session must not advance it."""
        chain = ChainResult(task_id="probe", total_sessions=2)
        chain.session_results = [
            SessionResult(session_number=1, branch_state=branch_state(1)),
            SessionResult(
                session_number=2, branch_state=branch_state(2), error="agent died"
            ),
        ]

        assert chain.sessions_completed == 1, "the errored session must not count"


class TestHealthySessionsStillSucceed:
    """Negative controls: the derived flag must not under-report a real session."""

    def test_a_session_that_commits_branch_state_reports_success(self, tmp_path):
        result = run_session(
            session_config=SessionConfig(session_number=1, prompt="p"),
            repos=[{"path": "repo-a"}],
            task_id="probe",
            workspace_root=str(tmp_path / "ws"),
            agent_callable=working_agent("agent output here"),
        )

        assert result.success is True, result.error
        assert result.error == ""
        assert result.branch_state is not None
        assert result.branch_state.branch_name == "eb-chain-probe-session-1"
        assert result.agent_output == "agent output here"

    def test_a_clean_session_result_reports_success_without_an_assignment(self):
        assert SessionResult(session_number=1, branch_state=branch_state()).success is True

    def test_a_default_session_result_has_not_succeeded(self):
        assert SessionResult(session_number=1).success is False
