"""Tests for the event_replay runner's contract with run_benchmark.

event_replay was 100% dead under run_benchmark before EnterpriseBench-78zt5, for
two independent reasons that this module pins down:

  1. Arg-type mismatch. run_benchmark dispatches every runner as
     ``<runner> <task.toml path>`` (its task list comes from an
     ``rglob("task.toml")``), but this runner declared and consumed its
     positional as a DIRECTORY — so it resolved ``.../task.toml/task.toml`` and
     died with FileNotFoundError before scoring anything.
  2. Silent stub. With the directory arg corrected, ``--agent-actions`` is not a
     passthrough flag, so run_benchmark never sends it and every dispatch lands
     in the "sandbox integration not yet implemented" branch. That branch
     returned normally and ``main()``'s return value was discarded at the
     ``__main__`` guard, so the process exited 0 — run_benchmark recorded
     status="completed" with score None. A stub reported as a real run.

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
from event_replay import resolve_task_dir  # noqa: E402


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


@pytest.fixture
def task_dir(tmp_path: Path) -> Path:
    """A minimal, valid event_replay task directory."""
    d = tmp_path / "er-test-001"
    d.mkdir()
    (d / "task.toml").write_text(TASK_TOML)
    _write_jsonl(d / "events.jsonl", EVENTS)
    _write_jsonl(d / "oracle_actions.jsonl", ORACLE_ACTIONS)
    return d


def _run(*args: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        [sys.executable, str(RUNNER), *args],
        capture_output=True,
        text=True,
    )


class TestResolveTaskDir:
    """The arg contract: run_benchmark sends a FILE, humans send a DIRECTORY."""

    def test_directory_resolves_to_itself(self, task_dir: Path) -> None:
        assert resolve_task_dir(task_dir) == task_dir.resolve()

    def test_task_toml_file_resolves_to_its_directory(self, task_dir: Path) -> None:
        assert resolve_task_dir(task_dir / "task.toml") == task_dir.resolve()

    def test_nonexistent_path_resolves_as_a_directory(self, tmp_path: Path) -> None:
        """A missing path is not a file, so it must fall through to load_task_config's
        FileNotFoundError rather than silently becoming its own parent."""
        missing = tmp_path / "nope"
        assert resolve_task_dir(missing) == missing.resolve()


class TestRunBenchmarkDispatchContract:
    """The exact invocation run_benchmark makes: a task.toml FILE path."""

    def test_task_toml_path_does_not_crash_on_load(self, task_dir: Path) -> None:
        """Regression: this raised FileNotFoundError on .../task.toml/task.toml,
        which disabled the event_replay session_type entirely."""
        proc = _run(str(task_dir / "task.toml"), "--mode", "baseline")
        assert "FileNotFoundError" not in proc.stderr
        assert "er-test-001" in proc.stdout

    def test_directory_path_still_works(self, task_dir: Path) -> None:
        """The docstring's documented usage must not regress while fixing the above."""
        proc = _run(str(task_dir), "--mode", "baseline")
        assert "FileNotFoundError" not in proc.stderr
        assert "er-test-001" in proc.stdout


class TestStubBranchFailsClosed:
    """No --agent-actions means nothing was scored — say so with the exit code."""

    def test_stub_exits_nonzero(self, task_dir: Path) -> None:
        """Regression: returning 0 here had run_benchmark record a "completed" run
        with score None. Nonzero routes it to run_benchmark's error channel."""
        proc = _run(str(task_dir / "task.toml"), "--mode", "baseline")
        assert proc.returncode != 0

    def test_stub_writes_no_results(self, task_dir: Path) -> None:
        """It scored nothing, so it must not leave a results.json claiming otherwise."""
        _run(str(task_dir / "task.toml"), "--mode", "baseline")
        assert not (task_dir / RESULTS_FILENAME).exists()

    def test_stub_explains_itself(self, task_dir: Path) -> None:
        proc = _run(str(task_dir / "task.toml"), "--mode", "baseline")
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

    @pytest.fixture
    def scored(self, task_dir: Path) -> Path:
        """Score a perfect run (agent actions == oracle) and return the task dir."""
        agent = _write_jsonl(task_dir / "agent_actions.jsonl", ORACLE_ACTIONS)
        proc = _run(
            str(task_dir / "task.toml"),
            "--agent-actions",
            str(agent),
            "--mode",
            "baseline",
        )
        assert proc.returncode == 0, proc.stderr
        return task_dir

    def test_results_json_is_written(self, scored: Path) -> None:
        assert (scored / RESULTS_FILENAME).exists()

    def test_results_carry_the_shared_shape(self, scored: Path) -> None:
        """Same contract every other runner writes — not an event_replay dialect."""
        payload = json.loads((scored / RESULTS_FILENAME).read_text())
        assert tuple(payload) == RESULTS_FIELDS

    def test_score_is_durable_not_just_printed(self, scored: Path) -> None:
        payload = json.loads((scored / RESULTS_FILENAME).read_text())
        assert payload["scores"]["task_score"] == pytest.approx(1.0)

    def test_scored_run_is_visible_to_skip_completed_and_headline(
        self, scored: Path
    ) -> None:
        """success gates run_sweep + is_task_completed; phase gates the headline."""
        payload = json.loads((scored / RESULTS_FILENAME).read_text())
        assert payload["success"] is True
        assert payload["phase"] == "complete"

    def test_task_metadata_comes_from_the_task_toml(self, scored: Path) -> None:
        payload = json.loads((scored / RESULTS_FILENAME).read_text())
        assert payload["task_id"] == "er-test-001"
        assert payload["task_metadata"]["suite"] == "incident_response"
        assert payload["task_metadata"]["languages"] == ["python"]

    def test_offline_scoring_reports_no_tool_usage(self, scored: Path) -> None:
        """This process spends no tokens; empty is honest, a fabricated 0.0 is not."""
        payload = json.loads((scored / RESULTS_FILENAME).read_text())
        assert payload["tool_usage"] == {}

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
