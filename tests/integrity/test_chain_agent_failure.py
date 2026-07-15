"""An AGENT failure is a score the model earned, not a run to throw away.

Bead 6c9wp closed the over-credit above this line: a chain whose sessions failed
must not be handed a total. It did so by routing EVERY failed session to the
re-run channel, because ``run_session`` caught any exception out of the agent
into one bare ``except Exception -> success=False`` and nothing downstream could
tell a broken sandbox from a model that blew its context budget.

That collapse re-opens the same over-credit through the exclusion door. A model
that reliably exhausts its budget on hard chains never receives the 0.0 it
earned: its failed runs leave the table entirely, and its reported mean is
computed only over the runs its agent survived. Upward bias, same family.

The opposite error is equally real, and is the bug ``scorer_guard`` exists to
prevent (beads s58f, hktt/pt0n): scoring an infra-crashed session 0.0 blames the
model for a broken harness. So the failure has to be TYPED at its source, by the
only party that knows which one it saw:

    AgentFailure declared by the adapter  ->  scored, stays in the table
    anything else out of the agent        ->  re-run channel, no score
    setup / commit (the harness's code)   ->  re-run channel, no score

Fail closed: an UNDECLARED exception is infra. Guessing budget exhaustion out of
an arbitrary traceback is how the false-zero bugs got in, and a wrong guess in
that direction blames the model for the harness.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(REPO_ROOT / "scripts"))
sys.path.insert(0, str(REPO_ROOT / "lib"))

from orchestration import session as session_mod
from orchestration.chain_runner import (
    SESSION_FAILURE_REASON,
    ChainResult,
    run_chain,
)
from orchestration.session import (
    AGENT_FAILURE,
    INFRA_FAILURE,
    AgentFailure,
    SessionConfig,
    run_session,
)

from .test_chain_session_failure import (
    agent_failing_at,
    scoring_verifier,
    task_def,
    verifier_ran,
    working_agent,
    write_verifier,
)


def disk_reading_checkpoints(task_dir: Path, marker: str) -> list[dict]:
    """A final checkpoint that scores what is ACTUALLY on disk: 1.0 if the agent
    left ``marker`` behind, 0.0 if it never got that far.

    The fixed-score verifier cannot carry these tests. It reports the same number
    whether the agent finished or died, so it could not distinguish a chain that
    scored its real work from one handed a fabricated total.
    """
    return [
        {
            "name": "cp",
            "weight": 1.0,
            "verifier": write_verifier(
                task_dir,
                "final.sh",
                f'#!/bin/sh\ntouch "{task_dir}/final.sh.ran"\n'
                f'if [ -f "$1/repo-a/{marker}" ]; then echo \'{{"score": 1.0}}\'\n'
                f"else echo '{{\"score\": 0.0}}'\nfi\nexit 0\n",
            ),
        }
    ]


def budget_exhausted() -> AgentFailure:
    """The bead's motivating failure: the model ran out of context on a hard
    chain. Real, attributable to the model, and worth exactly 0.0."""
    return AgentFailure(
        "context_budget_exhausted", "the agent exhausted its context budget"
    )


class TestAnAgentFailureIsScored:
    def test_a_declared_agent_failure_scores_the_chain_instead_of_re_running_it(
        self, tmp_path
    ):
        """THE BEAD. The agent declares it blew its budget in session 1 of 2.
        This used to become a ``session_failure``: total_score None, exit 1, the
        run dropped from the table and the earned 0.0 with it."""
        td = task_def(
            session_count=2,
            final_checkpoints=disk_reading_checkpoints(tmp_path, "session_1.md"),
        )

        result = run_chain(
            td,
            workspace_root=str(tmp_path / "ws"),
            agent_callable=agent_failing_at(1, budget_exhausted()),
            task_dir=str(tmp_path),
        )

        assert result.session_failure is None, (
            "the model failing is not an infrastructure fault — routing it to the "
            "re-run channel is what drops the zero it earned"
        )
        assert not result.is_invalid, "an agent failure has a score; it stays in the table"
        assert result.agent_failure is not None
        assert result.agent_failure["reason"] == "context_budget_exhausted"
        assert result.agent_failure["session_number"] == 1
        assert result.agent_failure["stage"] == "agent"
        assert result.agent_failure["detail"]

        assert verifier_ran(tmp_path), (
            "the checkpoints must measure what the model managed before it died"
        )
        assert result.total_score == pytest.approx(0.0), (
            "the agent produced nothing, so it earned nothing — a real 0.0"
        )
        assert result.final_score == pytest.approx(0.0)

    def test_a_late_agent_failure_does_not_score_from_the_milestones_it_passed(
        self, tmp_path
    ):
        """Scoring an agent failure must not become a NEW over-credit path. The
        agent passes session 1's milestone, then blows its budget in session 2;
        the score has to reflect the task it never finished, not the handoff it
        managed. The final checkpoints outweigh a milestone ten to one for
        exactly this reason."""
        td = task_def(
            session_count=2,
            final_checkpoints=disk_reading_checkpoints(tmp_path, "session_2.md"),
            session_milestones={
                1: [{"name": "m1", "verifier": scoring_verifier(tmp_path, 1.0, "m1.sh")}]
            },
        )

        result = run_chain(
            td,
            workspace_root=str(tmp_path / "ws"),
            agent_callable=agent_failing_at(2, budget_exhausted()),
            task_dir=str(tmp_path),
        )

        assert verifier_ran(tmp_path, "m1.sh"), "session 1's milestone really did pass"
        assert result.milestone_scores[0].total_score == pytest.approx(1.0)
        assert result.agent_failure["session_number"] == 2
        # (1.0 * 0.1) / (0.1 + 1.0) — the passed handoff, against the unfinished task.
        assert result.total_score == pytest.approx(0.0909, abs=0.001)

    def test_the_milestones_the_agent_never_reached_score_zero(self, tmp_path):
        """Dropping the un-run milestones instead would shrink the denominator and
        reweight the ones the agent DID pass — the partial mean ``SessionScore``
        refuses to compute, arriving one level up. The agent earned these zeros by
        not getting to them, so they are scored, not skipped."""
        td = task_def(
            session_count=3,
            final_checkpoints=disk_reading_checkpoints(tmp_path, "session_3.md"),
            session_milestones={
                1: [{"name": "m1", "verifier": scoring_verifier(tmp_path, 1.0, "m1.sh")}],
                2: [{"name": "m2", "verifier": scoring_verifier(tmp_path, 1.0, "m2.sh")}],
            },
        )

        result = run_chain(
            td,
            workspace_root=str(tmp_path / "ws"),
            agent_callable=agent_failing_at(2, budget_exhausted()),
            task_dir=str(tmp_path),
        )

        zeroed = [
            m
            for ms in result.milestone_scores
            for m in ms.milestones
            if m.milestone_name == "m2"
        ]
        assert len(zeroed) == 1, "session 2's milestone must still be in the denominator"
        assert zeroed[0].score == pytest.approx(0.0)
        assert zeroed[0].passed is False
        assert zeroed[0].infra_error is None, (
            "the agent not reaching a milestone is a zero it earned, not a verifier "
            "that broke — an infra_error here would invalidate the whole run"
        )
        assert not verifier_ran(tmp_path, "m2.sh"), (
            "the zero is a fill for a milestone that never ran, not a real verdict"
        )
        assert zeroed[0].not_reached is True, (
            "the fill must SAY it is a fill. milestone.py's own doctrine is that a "
            "placeholder 0.0 is a false zero; what makes this one honest is that the "
            "agent never got here, and a reader that cannot tell it from a verifier's "
            "real 0.0 cannot check that claim"
        )

        # (1.0 * 0.1 + 0.0 * 0.1 + 0.0 * 1.0) / 1.2 — with m2 dropped it would be
        # 0.1/1.1 = 0.0909, i.e. the agent rewarded for the milestone it skipped.
        assert result.total_score == pytest.approx(0.0833, abs=0.001)

    def test_a_milestone_the_agent_did_reach_is_not_marked_not_reached(self, tmp_path):
        """The negative control for the fill's provenance: a real verdict must not
        claim to be one, or the flag means nothing."""
        td = task_def(
            session_count=2,
            final_checkpoints=disk_reading_checkpoints(tmp_path, "session_2.md"),
            session_milestones={
                1: [{"name": "m1", "verifier": scoring_verifier(tmp_path, 1.0, "m1.sh")}]
            },
        )

        result = run_chain(
            td,
            workspace_root=str(tmp_path / "ws"),
            agent_callable=agent_failing_at(2, budget_exhausted()),
            task_dir=str(tmp_path),
        )

        assert verifier_ran(tmp_path, "m1.sh")
        real = [
            m
            for ms in result.milestone_scores
            for m in ms.milestones
            if m.milestone_name == "m1"
        ]
        assert real[0].not_reached is False

    def test_the_summary_does_not_render_a_fill_as_a_verdict(self, tmp_path):
        """`FAIL (0.00)` is what a verifier that ran and scored zero renders as.
        A milestone the agent never reached must not borrow that text."""
        td = task_def(
            session_count=3,
            final_checkpoints=disk_reading_checkpoints(tmp_path, "session_3.md"),
            session_milestones={
                2: [{"name": "m2", "verifier": scoring_verifier(tmp_path, 1.0, "m2.sh")}]
            },
        )

        text = run_chain(
            td,
            workspace_root=str(tmp_path / "ws"),
            agent_callable=agent_failing_at(2, budget_exhausted()),
            task_dir=str(tmp_path),
        ).summary()

        assert "m2: NOT REACHED" in text
        assert "m2: FAIL" not in text


class TestTheLastSessionsMilestonesAreExcludedFromBothPaths:
    """A chain never runs its FINAL session's milestones — the final checkpoints
    measure that session's work instead.

    The rule therefore has to hold identically on two paths: the healthy loop that
    RUNS milestones, and the zero-fill that scores the ones the agent never
    reached. Expressed as two separate comparisons they can drift apart, and the
    drift is silent — a fill for a milestone the healthy path would never have run
    is a penalty the design does not levy, invented only for agents that die.
    """

    def test_a_healthy_chain_does_not_run_them(self, tmp_path):
        result = run_chain(
            task_def(
                session_count=2,
                final_checkpoints=disk_reading_checkpoints(tmp_path, "session_2.md"),
                session_milestones={
                    2: [{"name": "last", "verifier": scoring_verifier(tmp_path, 1.0, "last.sh")}]
                },
            ),
            workspace_root=str(tmp_path / "ws"),
            agent_callable=working_agent(),
            task_dir=str(tmp_path),
        )

        assert not verifier_ran(tmp_path, "last.sh")
        assert not [
            m
            for ms in result.milestone_scores
            for m in ms.milestones
            if m.milestone_name == "last"
        ]

    def test_an_agent_failure_does_not_zero_fill_them(self, tmp_path):
        """The complement of the case above, and the one that drifts: the agent
        dies at the final session, whose milestones a healthy chain would never
        have run. They must not be conjured into the denominator as zeros."""
        result = run_chain(
            task_def(
                session_count=2,
                final_checkpoints=disk_reading_checkpoints(tmp_path, "session_2.md"),
                session_milestones={
                    2: [{"name": "last", "verifier": scoring_verifier(tmp_path, 1.0, "last.sh")}]
                },
            ),
            workspace_root=str(tmp_path / "ws"),
            agent_callable=agent_failing_at(2, budget_exhausted()),
            task_dir=str(tmp_path),
        )

        assert result.agent_failure is not None
        assert not verifier_ran(tmp_path, "last.sh")
        assert not [
            m
            for ms in result.milestone_scores
            for m in ms.milestones
            if m.milestone_name == "last"
        ], "the final session's milestones are not part of the chain's score on either path"
        # Only the final checkpoint counts: 0.0 * 1.0 / 1.0. A stray zero-fill would
        # add 0.1 to the denominator and change nothing about the numerator — the
        # score would stay 0.0 and hide the drift, which is why the assertion above
        # checks the milestone set itself, not just the number.
        assert result.total_score == pytest.approx(0.0)


class TestAnUndeclaredFailureStillFailsClosed:
    def test_a_bare_exception_out_of_the_agent_is_infra_not_a_zero(self, tmp_path):
        """The classification is DECLARED, never inferred. A harness that dies
        with a RuntimeError has said nothing about whose fault it was, and reading
        the model's guilt out of an arbitrary traceback is the guess that produced
        the false-zero bugs. No declaration, no score."""
        td = task_def(
            session_count=2,
            final_checkpoints=disk_reading_checkpoints(tmp_path, "session_1.md"),
        )

        result = run_chain(
            td,
            workspace_root=str(tmp_path / "ws"),
            agent_callable=agent_failing_at(1),  # bare RuntimeError
            task_dir=str(tmp_path),
        )

        assert result.agent_failure is None, "nothing may infer an agent failure"
        assert result.session_failure is not None
        assert result.session_failure["reason"] == SESSION_FAILURE_REASON
        assert result.is_invalid
        assert result.total_score is None
        assert not verifier_ran(tmp_path), (
            "an unclassified failure leaves a workspace of unknown provenance — the "
            "checkpoints must not score it"
        )


class TestRunSessionTypesTheFailureAtItsSource:
    """The classification lives at the stage that raised, because that is the only
    place that knows whose failure it is. ``chain_runner`` reads the type; it
    never re-derives it from a message string."""

    def _run(self, tmp_path, agent, repos=None):
        return run_session(
            session_config=SessionConfig(session_number=1, prompt="p"),
            repos=repos or [{"path": "repo-a"}],
            task_id="chain-agent-failure",
            workspace_root=str(tmp_path / "ws"),
            agent_callable=agent,
        )

    def test_a_declared_agent_failure_is_typed_agent(self, tmp_path):
        result = self._run(tmp_path, agent_failing_at(1, budget_exhausted()))

        assert result.success is False
        assert result.failure.kind == AGENT_FAILURE
        assert result.failure.is_agent is True
        assert result.failure.reason == "context_budget_exhausted"
        assert result.failure.stage == "agent"

    def test_an_undeclared_exception_from_the_agent_is_typed_infra(self, tmp_path):
        result = self._run(tmp_path, agent_failing_at(1))

        assert result.failure.kind == INFRA_FAILURE
        assert result.failure.is_agent is False
        assert result.failure.stage == "agent"
        assert "RuntimeError" in result.failure.detail, (
            "str(exc) is empty for a bare `raise IndexError` — an operator sent to "
            "re-run needs more than a blank"
        )

    def test_a_setup_failure_is_typed_infra(self, tmp_path):
        """The agent never even ran. Nothing here can be the model's fault."""
        result = self._run(tmp_path, working_agent(), repos=[{"path": "../escape"}])

        assert result.failure.kind == INFRA_FAILURE
        assert result.failure.stage == "setup"

    def test_a_commit_failure_is_typed_infra(self, tmp_path, monkeypatch):
        """The agent already finished. Git breaking afterwards is the harness's
        fault, and scoring it 0.0 would be the false zero of bead s58f."""

        def explode(**kwargs):
            raise OSError("git is broken")

        monkeypatch.setattr(session_mod, "commit_session_state", explode)

        result = self._run(tmp_path, working_agent())

        assert result.failure.kind == INFRA_FAILURE
        assert result.failure.stage == "commit"

    def test_an_agent_failure_while_simulating_is_infra(self, tmp_path, monkeypatch):
        """There is no model in simulate mode, so there is nobody to charge a zero
        to. The scaffold raising AgentFailure would otherwise be typed AGENT — the
        stage is 'agent' — and scored as a model's earned failure on a run where no
        model ever ran. Harness code cannot confess on the agent's behalf."""

        def explode(**kwargs):
            raise AgentFailure("context_budget_exhausted", "scaffold says so")

        monkeypatch.setattr(session_mod, "simulate_agent_work", explode)

        result = run_session(
            session_config=SessionConfig(session_number=1, prompt="p"),
            repos=[{"path": "repo-a"}],
            task_id="chain-agent-failure",
            workspace_root=str(tmp_path / "ws"),
            simulate=True,
        )

        assert result.failure.kind == INFRA_FAILURE
        assert result.failure.stage == "agent"

    def test_a_completed_session_carries_no_failure(self, tmp_path):
        result = self._run(tmp_path, working_agent())

        assert result.failure is None
        assert result.success is True
        assert result.error == ""

    def test_an_agent_failure_raised_outside_the_agent_stage_is_infra(
        self, tmp_path, monkeypatch
    ):
        """The whole design rests on 'only the adapter driving the model may say
        the model failed'. Nothing checks WHERE it was said, so harness code that
        happens to raise AgentFailure — a helper reusing the name, a stray import —
        would be typed AGENT and scored as the model's zero, on a session the model
        may have completed. The stage that raised is the invariant; enforce it here
        rather than asserting it in a docstring."""

        def explode(**kwargs):
            raise AgentFailure("not_the_agents_fault", "commit stage raised this")

        monkeypatch.setattr(session_mod, "commit_session_state", explode)

        result = self._run(tmp_path, working_agent())

        assert result.failure.kind == INFRA_FAILURE, (
            "an AgentFailure from the commit stage is a harness bug wearing the "
            "agent's name — scoring it 0.0 is the false zero of bead s58f"
        )
        assert result.failure.stage == "commit"


class TestAgentFailureJsonContract:
    """The file a downstream reader parses, and the exit code ``run_benchmark``
    routes on.

    Driven through ``main`` with ``run_chain`` stubbed, because no agent is wired
    into the CLI yet (bead bl4bc): ``--agent`` is accepted and discarded, so a
    real end-to-end run cannot reach an agent failure today. This pins the
    serialization contract for when one is.
    """

    def _task_toml(self, tmp_path: Path) -> Path:
        toml_path = tmp_path / "task.toml"
        toml_path.write_text(
            "[task]\n"
            'id = "chain-agent-failure"\n'
            'suite = "customer_escalation"\n'
            'difficulty = "medium"\n'
            'session_type = "chain"\n'
            "session_count = 2\n"
            "\n"
            "[[sessions]]\n"
            'prompt = "session 1"\n'
            "\n"
            "[[sessions]]\n"
            'prompt = "session 2"\n'
            "\n"
            "[[checkpoints]]\n"
            'name = "cp"\n'
            "weight = 1.0\n"
            f'verifier = "{scoring_verifier(tmp_path, 1.0)}"\n'
        )
        return toml_path

    def test_the_earned_zero_is_written_with_a_zero_exit(self, tmp_path, monkeypatch):
        from orchestration import chain_runner

        task_toml = self._task_toml(tmp_path)

        def fake_run_chain(task_def, **kwargs):
            result = ChainResult(task_id=task_def.task_id, total_sessions=2)
            result.total_score = 0.0
            result.final_score = 0.0
            result.agent_failure = {
                "reason": "context_budget_exhausted",
                "stage": "agent",
                "detail": "the agent exhausted its context budget",
                "session_number": 1,
            }
            return result

        monkeypatch.setattr(chain_runner, "run_chain", fake_run_chain)
        monkeypatch.setattr(sys, "argv", ["chain_runner", str(task_toml)])

        assert chain_runner.main() == 0, (
            "an agent failure is a valid measurement — a nonzero exit would route it "
            "to run_benchmark's error channel and drop it from the results table, "
            "which is the exclusion bias this bead is about"
        )

        payload = json.loads((tmp_path / "chain_result.json").read_text())
        assert payload["total_score"] == pytest.approx(0.0), (
            "the zero the model earned must reach the results table as a number"
        )
        assert payload["agent_failure"]["reason"] == "context_budget_exhausted"
        assert payload["agent_failure"]["session_number"] == 1
        assert payload["agent_failure"]["stage"] == "agent"
        assert payload["agent_failure"]["detail"]
        assert "session_failure" not in payload

    def test_the_summary_reports_the_failure_alongside_the_score(self, tmp_path):
        td = task_def(
            session_count=2,
            final_checkpoints=disk_reading_checkpoints(tmp_path, "session_1.md"),
        )

        text = run_chain(
            td,
            workspace_root=str(tmp_path / "ws"),
            agent_callable=agent_failing_at(1, budget_exhausted()),
            task_dir=str(tmp_path),
        ).summary()

        assert "AGENT FAILURE" in text
        assert "context_budget_exhausted" in text
        assert "INVALID RUN" not in text, "the run is valid; the agent failed it"
        assert "Total score: 0.00" in text, (
            "the score must render as the number it is, not as NOT SCORED"
        )


class TestHealthyChainStillScores:
    def test_an_agent_that_survives_is_untouched(self, tmp_path):
        """The negative control: nothing above may make a working chain report an
        agent failure or lose its score."""
        result = run_chain(
            task_def(
                session_count=2,
                final_checkpoints=disk_reading_checkpoints(tmp_path, "session_2.md"),
            ),
            workspace_root=str(tmp_path / "ws"),
            agent_callable=working_agent(),
            task_dir=str(tmp_path),
        )

        assert result.agent_failure is None
        assert result.session_failure is None
        assert not result.is_invalid
        assert result.total_score == pytest.approx(1.0)
