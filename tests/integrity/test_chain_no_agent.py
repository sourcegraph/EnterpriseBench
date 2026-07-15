"""A chain with no agent wired into it has no score.

The default path, not a failure path. ``main()`` never passed ``agent_callable``
to ``run_chain`` (``--agent`` was parsed and discarded), ``run_chain`` collapsed
it to ``None`` either way, and ``run_session`` read ``None`` as "simulate". So a
chain task run WITHOUT ``--simulate`` still simulated: every session "succeeded"
against a marker file, the final checkpoints scored that marker file, and the run
exited 0 and was recorded ``completed`` by ``run_benchmark``:

    Session 1: OK -> chain/session-1
    Session 2: OK -> chain/session-2
    Total score: 0.00      exit 0   ->  status="completed"

Nothing about that 0.00 came from an agent. It is the same fabrication family as
6c9wp (which closed the *failed*-session path), reached through the path every
real run takes. 6c9wp's gate cannot catch it: the simulated sessions genuinely
succeed, so ``sessions_completed == session_count`` and the gate passes.

Fail closed: no agent and no explicit ``simulate`` is an INVALID run — zero
sessions, no score, nonzero exit — routed to the re-run channel through its own
``agent_not_configured`` cause, because "wire up the harness" is a different
remedy from "the agent died, re-run it".
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

from orchestration import session as session_mod
from orchestration.chain_runner import NO_AGENT_REASON, ChainTaskDefinition, run_chain
from orchestration.session import SessionConfig, run_session

from .test_chain_session_failure import (  # reuse the sentinel helpers
    final_checkpoints,
    scoring_verifier,
    verifier_ran,
    working_agent,
)
from .test_chain_session_failure import chain_task_toml as _chain_task_toml
from .test_chain_session_failure import task_def as _chain_task_def


def task_def(
    final_checkpoints: list[dict] | None = None,
    session_milestones: dict[int, list[dict]] | None = None,
) -> ChainTaskDefinition:
    """The shared 2-session builder, under this module's task id."""
    return _chain_task_def(
        session_count=2,
        final_checkpoints=final_checkpoints,
        session_milestones=session_milestones,
        task_id="chain-no-agent",
    )


def chain_task_toml(task_dir: Path, verifier: str | None = None) -> Path:
    """The shared 2-session TOML builder, under this module's task id."""
    return _chain_task_toml(task_dir, verifier=verifier, task_id="chain-no-agent")


def run_cli(task_toml: Path, *flags: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        [sys.executable, "-m", "orchestration.chain_runner", str(task_toml), *flags],
        capture_output=True,
        text=True,
        cwd=str(REPO_ROOT / "scripts"),
        timeout=120,
    )


def simulate_markers(workspace: Path) -> list[Path]:
    """Every file ``simulate_agent_work`` leaves behind, anywhere in the workspace."""
    return sorted(workspace.rglob("session_*_output.md"))


class TestNoAgentIsNeverAScore:
    def test_no_agent_and_no_simulate_runs_zero_sessions_and_scores_nothing(
        self, tmp_path
    ):
        """THE BUG. This call is exactly what main() made: no agent, no simulate.
        It used to simulate both sessions, score the markers, and return a total."""
        final = scoring_verifier(tmp_path, 1.0)
        milestone = scoring_verifier(tmp_path, 1.0, name="m1.sh")
        td = task_def(
            final_checkpoints=[{"name": "cp", "weight": 1.0, "verifier": final}],
            session_milestones={1: [{"name": "m1", "verifier": milestone}]},
        )
        workspace = tmp_path / "ws"

        result = run_chain(
            td,
            workspace_root=str(workspace),
            task_dir=str(tmp_path),
        )

        assert result.agent_not_configured is not None
        assert result.agent_not_configured["reason"] == NO_AGENT_REASON
        assert result.agent_not_configured["cause"] == NO_AGENT_REASON
        assert result.agent_not_configured["stage"] == "chain"
        assert result.agent_not_configured["detail"]
        assert result.is_invalid

        assert result.total_score is None, "a chain with no agent has no score"
        assert result.final_score is None

        # Score assertions alone would also pass for a fix that merely zeroed the
        # number. These show no session, and no verifier, ever ran.
        assert result.session_results == [], "no session may run without an agent"
        assert result.sessions_completed == 0
        assert result.milestone_scores == []
        assert not verifier_ran(tmp_path), "the final checkpoint must not verify"
        assert not verifier_ran(tmp_path, "m1.sh"), "the milestone must not verify"
        assert simulate_markers(workspace) == [], "nothing may be simulated"

    def test_summary_renders_invalid_run_instead_of_a_number(self, tmp_path):
        text = run_chain(
            task_def(
                final_checkpoints=[
                    {
                        "name": "cp",
                        "weight": 1.0,
                        "verifier": scoring_verifier(tmp_path, 1.0),
                    }
                ]
            ),
            workspace_root=str(tmp_path / "ws"),
            task_dir=str(tmp_path),
        ).summary()

        assert "INVALID RUN" in text
        assert "0.00" not in text, "an unscoreable run must not render as a zero"
        assert "1.00" not in text

    def test_an_agent_and_simulate_together_is_rejected_not_silently_resolved(
        self, tmp_path
    ):
        """The mirror image: run_chain used to DISCARD a real agent whenever
        simulate was also set, and score the simulation as if the agent had run."""
        with pytest.raises(ValueError, match="simulate"):
            run_chain(
                task_def(final_checkpoints=final_checkpoints(tmp_path)),
                workspace_root=str(tmp_path / "ws"),
                simulate=True,
                agent_callable=working_agent(),
                task_dir=str(tmp_path),
            )

        assert simulate_markers(tmp_path / "ws") == []


class TestRunSessionRefusesToSimulateByDefault:
    """Defense in depth. ``agent_callable=None`` meant "simulate" at the session
    layer, which is what let the missing wire upstream become a scored run. Any
    caller must now say ``simulate=True`` out loud."""

    def test_no_agent_and_no_simulate_raises_and_never_reaches_the_simulator(
        self, tmp_path, monkeypatch
    ):
        def explode(*args, **kwargs):
            raise AssertionError("simulate_agent_work must not be reached")

        monkeypatch.setattr(session_mod, "simulate_agent_work", explode)

        with pytest.raises(ValueError, match="agent"):
            run_session(
                session_config=SessionConfig(session_number=1, prompt="p"),
                repos=[{"path": "repo-a"}],
                task_id="chain-no-agent",
                workspace_root=str(tmp_path / "ws"),
            )

    def test_an_agent_and_simulate_together_is_rejected(self, tmp_path):
        with pytest.raises(ValueError, match="simulate"):
            run_session(
                session_config=SessionConfig(session_number=1, prompt="p"),
                repos=[{"path": "repo-a"}],
                task_id="chain-no-agent",
                workspace_root=str(tmp_path / "ws"),
                simulate=True,
                agent_callable=working_agent(),
            )


class TestNoAgentJsonContract:
    """End-to-end through the CLI: the file a downstream reader parses, and the
    exit code ``run_benchmark`` routes on."""

    def test_the_flags_run_benchmark_actually_sends_produce_no_score(self, tmp_path):
        """``collect_passthrough_args`` (run_benchmark.py) sends --agent, --mode,
        --account and --timeout. --agent must stay ACCEPTED — rejecting it at
        argparse would exit 2 before the guard, write no chain_result.json, and
        leave a stale false score on disk."""
        task_toml = chain_task_toml(tmp_path)

        proc = run_cli(
            task_toml,
            "--mode",
            "baseline",
            "--agent",
            "claude",
            "--account",
            "1",
            "--timeout",
            "3600",
            "--workspace",
            str(tmp_path / "ws"),
        )

        assert proc.returncode != 0, "a chain with no agent must not exit 0"

        payload = json.loads((tmp_path / "chain_result.json").read_text())
        assert payload["total_score"] is None
        assert payload["sessions_completed"] == 0
        assert payload["sessions_total"] == 2
        assert payload["agent_not_configured"]["reason"] == NO_AGENT_REASON
        assert payload["agent_not_configured"]["cause"] == NO_AGENT_REASON
        assert payload["agent_not_configured"]["stage"] == "chain"
        assert payload["agent_not_configured"]["detail"]

        assert not verifier_ran(tmp_path)
        assert simulate_markers(tmp_path / "ws") == []

    def test_a_stale_result_file_is_removed_before_the_run(self, tmp_path):
        """main() wrote the result only after run_chain returned, so any raise on
        the way (a bad task.toml, a rejected session_count) left an earlier run's
        false score sitting on disk as the current answer."""
        stale = tmp_path / "chain_result.json"
        stale.write_text(json.dumps({"task_id": "chain-no-agent", "total_score": 1.0}))
        task_toml = chain_task_toml(tmp_path)

        proc = run_cli(task_toml, "--workspace", str(tmp_path / "ws"))

        assert proc.returncode != 0
        payload = json.loads(stale.read_text())
        assert payload["total_score"] is None, "the stale 1.0 must not survive"

    def test_dry_run_does_not_execute_the_chain(self, tmp_path):
        """--dry-run was parsed and discarded, so a direct dry run executed the
        chain and wrote a result file."""
        task_toml = chain_task_toml(tmp_path)

        proc = run_cli(
            task_toml, "--dry-run", "--simulate", "--workspace", str(tmp_path / "ws")
        )

        assert proc.returncode == 0
        assert not (tmp_path / "chain_result.json").exists()
        assert not verifier_ran(tmp_path)
        assert simulate_markers(tmp_path / "ws") == []


class TestSimulationStaysAvailableButIsMarked:
    def test_simulate_still_scores_and_the_result_says_it_was_simulated(
        self, tmp_path
    ):
        """The negative control, and C3: --simulate wrote a plain total_score into
        the (git-tracked) task directory with nothing recording that no agent ran.
        The number is real for what it measured; the artifact has to say what that
        was."""
        task_toml = chain_task_toml(tmp_path)

        proc = run_cli(task_toml, "--simulate", "--workspace", str(tmp_path / "ws"))

        assert proc.returncode == 0
        payload = json.loads((tmp_path / "chain_result.json").read_text())
        assert payload["simulated"] is True, "a simulated result must say so"
        assert payload["total_score"] == pytest.approx(1.0)
        assert verifier_ran(tmp_path)

    def test_the_simulated_flag_travels_on_the_result_not_just_the_cli_payload(
        self, tmp_path
    ):
        """main() used to synthesize the flag from its own argv, so a ChainResult
        handed to any other caller carried a score with nothing saying an agent
        never produced it."""
        td = task_def(
            final_checkpoints=[
                {"name": "cp", "weight": 1.0, "verifier": scoring_verifier(tmp_path, 1.0)}
            ]
        )

        simulated = run_chain(
            td,
            workspace_root=str(tmp_path / "ws"),
            simulate=True,
            task_dir=str(tmp_path),
        )
        assert simulated.simulated is True
        assert simulated.total_score == pytest.approx(1.0)

    def test_a_real_agent_still_scores(self, tmp_path):
        """The fix must not stop a chain that HAS an agent from scoring."""
        result = run_chain(
            task_def(
                final_checkpoints=[
                    {
                        "name": "cp",
                        "weight": 1.0,
                        "verifier": scoring_verifier(tmp_path, 0.5),
                    }
                ]
            ),
            workspace_root=str(tmp_path / "ws"),
            agent_callable=working_agent(),
            task_dir=str(tmp_path),
        )

        assert not result.is_invalid
        assert result.agent_not_configured is None
        assert result.simulated is False, "an agent-earned score is not a simulated one"
        assert result.total_score == pytest.approx(0.5)
        assert verifier_ran(tmp_path)


class TestRunBenchmarkRecordsAnError:
    """The routing that made this bug publishable: run_benchmark reads the exit
    code and records status='completed' on 0. Nothing tested chain routing at all."""

    def test_a_chain_with_no_agent_is_recorded_as_error_not_completed(self, tmp_path):
        from run_benchmark import TaskInfo, run_task

        task_toml = chain_task_toml(tmp_path)
        task = TaskInfo(
            task_id="chain-no-agent",
            suite="customer_escalation",
            difficulty="medium",
            session_type="chain",
            task_type="error_provenance",
            toml_path=task_toml,
        )

        result = run_task(
            task,
            passthrough_args=["--mode", "baseline", "--agent", "claude"],
            mode="baseline",
        )

        assert result.status == "error", (
            "a chain that never ran an agent must never be recorded as completed"
        )
        assert result.score is None
        assert not verifier_ran(tmp_path)


class TestSimulateCannotReachABenchmarkRun:
    """Containment for the one remaining score-publishing simulation path: it is
    only out of reach of a sweep because run_benchmark has no --simulate flag to
    forward. That accident is now an assertion — the same species of accident (an
    argparse gap) is what silently guarded every sweep until --mode was added."""

    def test_run_benchmark_never_forwards_simulate(self):
        from run_benchmark import build_parser, collect_passthrough_args

        parser = build_parser()
        with pytest.raises(SystemExit):
            parser.parse_args(["benchmarks", "--simulate"])

        args = parser.parse_args(
            ["benchmarks", "--agent", "claude", "--mode", "hybrid", "--timeout", "60"]
        )
        assert "--simulate" not in collect_passthrough_args(args)
