"""Tests for the event_replay runner's contract with run_benchmark.

event_replay was 100% dead under run_benchmark before EnterpriseBench-78zt5, for
two independent reasons that this module pins down:

  1. Arg-type mismatch. run_benchmark dispatches every runner as
     ``<runner> <task.toml path>`` (its task list comes from an
     ``rglob("task.toml")``), but this runner declared and consumed its
     positional as a DIRECTORY — so it resolved ``.../task.toml/task.toml`` and
     died with FileNotFoundError before scoring anything. It now declares a
     task.toml FILE, the contract run_task and chain_runner already share.
  2. Silent stub. With the directory arg corrected, ``--agent-actions`` is not a
     passthrough flag, so run_benchmark never sends it and every dispatch lands
     in the "sandbox integration not yet implemented" branch. That branch
     returned normally and ``main()``'s return value was discarded at the
     ``__main__`` guard, so the process exited 0 — run_benchmark recorded
     status="completed" with score None. A stub reported as a real run.

The last two classes pin the asymmetry the runner has to hold: a broken TASK
fixture is invalid and re-run, a broken AGENT still earns the low score it made.

The exit-code tests run the runner as a real subprocess on purpose. Calling
``main()`` in-process would return 1 happily even while the shipped entrypoint
threw the value away; only ``sys.exit(main())`` makes the code reach the caller,
and only a subprocess observes what run_benchmark observes.
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
RUNNER = REPO_ROOT / "scripts" / "orchestration" / "event_replay.py"

sys.path.insert(0, str(REPO_ROOT / "scripts"))
sys.path.insert(0, str(REPO_ROOT / "scripts" / "orchestration"))

from results_io import RESULTS_FIELDS, RESULTS_FILENAME  # noqa: E402


TASK_TOML = """\
[task]
id = "er-test-001"
suite = "incident_response"
task_type = "incident_investigation"
difficulty = "medium"
session_type = "event_replay"
description = "fixture"
prompt = "fixture"

[metadata]
languages = ["python"]
"""

EVENTS = [
    {
        "timestamp_ms": 0,
        "event_type": "build_failure",
        "category": "cicd",
        "source": "github-actions",
        "severity": "warning",
        "summary": "CI failed: 2 tests broken",
        "payload": {"repo": "demo", "failed_tests": ["test_a", "test_b"]},
        "id": "evt-001",
    },
]

ORACLE_ACTIONS = [
    {
        "timestamp_ms": 5000,
        "action_type": "investigate",
        "target": "demo/tests/test_a.py",
        "description": "Investigate the broken tests",
        "triggered_by": ["evt-001"],
        "id": "act-001",
    },
]


def _write_jsonl(path: Path, rows: list[dict]) -> Path:
    path.write_text("".join(json.dumps(r) + "\n" for r in rows))
    return path


def _make_task_dir(d: Path) -> Path:
    """Populate *d* as a minimal, valid event_replay task directory."""
    d.mkdir(parents=True)
    (d / "task.toml").write_text(TASK_TOML)
    _write_jsonl(d / "events.jsonl", EVENTS)
    _write_jsonl(d / "oracle_actions.jsonl", ORACLE_ACTIONS)
    return d


@pytest.fixture
def task_dir(tmp_path: Path) -> Path:
    """A minimal, valid event_replay task directory."""
    return _make_task_dir(tmp_path / "er-test-001")


def _run(*args: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        [sys.executable, str(RUNNER), *args],
        capture_output=True,
        text=True,
    )


@pytest.fixture(scope="module")
def dispatched(tmp_path_factory: pytest.TempPathFactory) -> tuple:
    """One run of the exact invocation run_benchmark makes: a task.toml FILE and
    no --agent-actions, which is the branch every dispatched task lands in.

    Module-scoped: the tests below read this one result from different angles
    rather than each re-spawning the identical command.
    """
    d = _make_task_dir(tmp_path_factory.mktemp("dispatched") / "er-test-001")
    return _run(str(d / "task.toml"), "--mode", "baseline"), d


@pytest.fixture(scope="module")
def scored_dir(tmp_path_factory: pytest.TempPathFactory) -> Path:
    """Task dir of one scored perfect run (agent actions == oracle).

    Module-scoped for the same reason as ``dispatched``: its consumers only read
    the results.json this leaves behind, so one run serves all of them.
    """
    d = _make_task_dir(tmp_path_factory.mktemp("scored") / "er-test-001")
    agent = _write_jsonl(d / "agent_actions.jsonl", ORACLE_ACTIONS)
    proc = _run(
        str(d / "task.toml"),
        "--agent-actions",
        str(agent),
        "--mode",
        "baseline",
    )
    assert proc.returncode == 0, proc.stderr
    return d


@pytest.fixture(scope="module")
def scored(scored_dir: Path) -> dict:
    """The results.json payload that run left behind."""
    return json.loads((scored_dir / RESULTS_FILENAME).read_text())


class TestRunBenchmarkDispatchContract:
    """The exact invocation run_benchmark makes: a task.toml FILE path."""

    def test_task_toml_path_does_not_crash_on_load(self, dispatched: tuple) -> None:
        """Regression: this raised FileNotFoundError on .../task.toml/task.toml,
        which disabled the event_replay session_type entirely."""
        proc, _ = dispatched
        assert "FileNotFoundError" not in proc.stderr
        assert "er-test-001" in proc.stdout


class TestStubBranchFailsClosed:
    """No --agent-actions means nothing was scored — say so with the exit code."""

    def test_stub_exits_nonzero(self, dispatched: tuple) -> None:
        """Regression: returning 0 here had run_benchmark record a "completed" run
        with score None. Nonzero routes it to run_benchmark's error channel."""
        proc, _ = dispatched
        assert proc.returncode != 0

    def test_stub_writes_no_results(self, dispatched: tuple) -> None:
        """It scored nothing, so it must not leave a results.json claiming otherwise."""
        _, d = dispatched
        assert not (d / RESULTS_FILENAME).exists()

    def test_stub_explains_itself(self, dispatched: tuple) -> None:
        proc, _ = dispatched
        assert "not yet implemented" in proc.stdout

    def test_missing_agent_actions_file_exits_nonzero(self, task_dir: Path) -> None:
        proc = _run(
            str(task_dir / "task.toml"),
            "--agent-actions",
            str(task_dir / "absent.jsonl"),
            "--mode",
            "baseline",
        )
        assert proc.returncode != 0
        assert "not found" in proc.stderr


class TestScoreModeWritesResults:
    """The score was printed to stdout and nowhere else before this bead."""

    def test_results_json_is_written(self, scored_dir: Path) -> None:
        assert (scored_dir / RESULTS_FILENAME).exists()

    def test_results_carry_the_shared_shape(self, scored: dict) -> None:
        """Same contract every other runner writes — not an event_replay dialect."""
        assert tuple(scored) == RESULTS_FIELDS

    def test_score_is_durable_not_just_printed(self, scored: dict) -> None:
        assert scored["scores"]["task_score"] == pytest.approx(1.0)

    def test_scored_run_is_visible_to_skip_completed_and_headline(
        self, scored: dict
    ) -> None:
        """success gates run_sweep + is_task_completed; phase gates the headline."""
        assert scored["success"] is True
        assert scored["phase"] == "complete"

    def test_task_metadata_comes_from_the_task_toml(self, scored: dict) -> None:
        assert scored["task_id"] == "er-test-001"
        assert scored["task_metadata"]["suite"] == "incident_response"
        assert scored["task_metadata"]["languages"] == ["python"]

    def test_offline_scoring_reports_no_tool_usage(self, scored: dict) -> None:
        """This process spends no tokens; empty is honest, a fabricated 0.0 is not."""
        assert scored["tool_usage"] == {}

    def test_imperfect_run_scores_below_one(self, task_dir: Path) -> None:
        """Guard against a write that hardcodes success rather than reading the score."""
        _write_jsonl(
            task_dir / "agent_actions.jsonl",
            [{**ORACLE_ACTIONS[0], "target": "wrong/file.py", "id": "act-x"}],
        )
        proc = _run(
            str(task_dir / "task.toml"),
            "--agent-actions",
            str(task_dir / "agent_actions.jsonl"),
            "--mode",
            "baseline",
        )
        assert proc.returncode == 0, proc.stderr
        payload = json.loads((task_dir / RESULTS_FILENAME).read_text())
        assert payload["scores"]["task_score"] < 1.0


class TestBrokenFixtureIsInvalidNotScored:
    """The events file is a TASK FIXTURE the agent did not author.

    A malformed one breaks the verifier's own inputs, so it takes run_task's
    contract: verifier_infra_error + status=invalid, and the run goes to the
    re-run channel rather than the results table.
    """

    @pytest.fixture
    def broken_fixture_run(self, task_dir: Path) -> tuple:
        """Non-monotonic timestamps: the ordering the scorer's timeliness axis is
        computed against does not hold, so no verdict it reaches is trustworthy."""
        _write_jsonl(
            task_dir / "events.jsonl",
            [EVENTS[0], {**EVENTS[0], "timestamp_ms": -5000, "id": "evt-002"}],
        )
        agent = _write_jsonl(task_dir / "agent_actions.jsonl", ORACLE_ACTIONS)
        proc = _run(
            str(task_dir / "task.toml"),
            "--agent-actions",
            str(agent),
            "--mode",
            "baseline",
        )
        payload_path = task_dir / RESULTS_FILENAME
        payload = json.loads(payload_path.read_text()) if payload_path.exists() else {}
        return proc, payload

    def test_broken_fixture_exits_nonzero(self, broken_fixture_run: tuple) -> None:
        """The exit code is the gate: run_benchmark returns status="error" on
        nonzero BEFORE it reads scores.task_score, which is an unguarded read."""
        proc, _ = broken_fixture_run
        assert proc.returncode == 1

    def test_broken_fixture_is_not_a_completed_run(self, broken_fixture_run: tuple) -> None:
        _, payload = broken_fixture_run
        assert payload["success"] is False
        assert payload["phase"] == ""
        assert payload["status"] == "invalid"

    def test_broken_fixture_reports_the_verifier_as_the_cause(
        self, broken_fixture_run: tuple
    ) -> None:
        """Named as run_task names the same cause, so one vocabulary classifies
        every runner's infra failure — and the error says which fixture broke."""
        _, payload = broken_fixture_run
        assert payload["failure_class"] == "verifier_infra_error"
        assert "timestamp" in payload["error"]

    def test_broken_fixture_carries_the_shared_shape(self, broken_fixture_run: tuple) -> None:
        """The invalid write is the same contract as the scored one, not a
        stripped-down variant that starves the readers it skips."""
        _, payload = broken_fixture_run
        assert tuple(payload) == RESULTS_FIELDS


class TestBadAgentActionsAreStillScored:
    """The agent's action file is AGENT OUTPUT, and the opposite call.

    Routing its validation errors to `invalid` would let a broken agent escape
    the low score it earned into the re-run channel — the integrity hole above,
    pointing the other way. Only the task's own fixtures invalidate a run.
    """

    def test_unknown_action_type_warns_but_scores(self, task_dir: Path) -> None:
        agent = _write_jsonl(
            task_dir / "agent_actions.jsonl",
            [{**ORACLE_ACTIONS[0], "action_type": "teleport"}],
        )
        proc = _run(
            str(task_dir / "task.toml"),
            "--agent-actions",
            str(agent),
            "--mode",
            "baseline",
        )
        assert proc.returncode == 0, proc.stderr
        assert "unknown action_type" in proc.stderr

        payload = json.loads((task_dir / RESULTS_FILENAME).read_text())
        assert payload["status"] == ""
        assert payload["failure_class"] is None
        assert payload["success"] is True
        # It earned a score, and a worse one than the oracle-matching run.
        assert payload["scores"]["task_score"] < 1.0
