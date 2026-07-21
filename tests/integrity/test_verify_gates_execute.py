"""`make verify-crnt` and `make verify-expected-solutions` must actually run.

Both targets called their underlying validator with zero positional
arguments — `crnt_validator.py` (single-task by design: one positional
task.toml, one process, per its own docstring) and
`validate_expected_solutions.py` (a directory-or-file positional) both
require that argument. Invoked bare, each died on argparse's "the following
arguments are required" before validating a single task, and `make`
propagated that as a build error rather than a validation verdict — so the
gate never ran, on any push, regardless of what the task corpus contained.

These tests pin two things per gate, mirroring tests/test_makefile_test_target.py:
  1. the target reaches the real task corpus (count > 0, no argparse usage error)
  2. the target's underlying command still fails loudly on a seeded violation,
     using the CRNT_TASK_TOMLS / EXPECTED_SOLUTIONS_PATH overrides added
     alongside the fix (same override convention as PYTEST_PATHS)

See EnterpriseBench Lane A audit.
"""

from __future__ import annotations

import shutil
import subprocess
import textwrap
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]

pytestmark = pytest.mark.skipif(
    shutil.which("make") is None, reason="make is not installed"
)


def _make(*args: str, timeout: int = 120) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["make", *args],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        timeout=timeout,
    )


def _output(result: subprocess.CompletedProcess[str]) -> str:
    return f"{result.stdout[-3000:]}{result.stderr[-3000:]}"


# ---------------------------------------------------------------------------
# Fixtures: seeded multi-repo task.tomls for verify-crnt
# ---------------------------------------------------------------------------

_GOOD_CRNT_TOML = textwrap.dedent(
    """\
    [task]
    id = "seeded-crnt-good"
    suite = "customer_escalation"

    [[repos]]
    url = "https://github.com/org/repo-a"
    rev = "v1"
    path = "repo-a"

    [[repos]]
    url = "https://github.com/org/repo-b"
    rev = "v1"
    path = "repo-b"

    [[ground_truth.required_files]]
    repo = "repo-a"
    path = "src/a.py"

    [[ground_truth.required_files]]
    repo = "repo-b"
    path = "src/b.py"
    """
)

_BAD_CRNT_TOML = textwrap.dedent(
    """\
    [task]
    id = "seeded-crnt-bad"
    suite = "customer_escalation"

    [[repos]]
    url = "https://github.com/org/repo-a"
    rev = "v1"
    path = "repo-a"

    [[repos]]
    url = "https://github.com/org/repo-b"
    rev = "v1"
    path = "repo-b"

    [[ground_truth.required_files]]
    repo = "repo-a"
    path = "src/a.py"
    """
)


class TestVerifyCRNT:
    def test_reaches_the_real_corpus(self) -> None:
        """Regression guard: the old bare invocation errored at argparse
        before checking a single task, so this must show real work happened.
        """
        result = _make("verify-crnt")
        out = _output(result)

        assert "the following arguments are required" not in out.lower(), (
            f"verify-crnt hit argparse's usage error instead of running — "
            f"the gate never reached a single task\n{out}"
        )
        assert "[verify-crnt] checked" in out, f"no task-count summary line\n{out}"

        checked = int(out.split("[verify-crnt] checked")[1].split("tasks")[0].strip())
        assert checked > 0, f"verify-crnt checked 0 tasks\n{out}"

    def test_empty_task_list_fails_loudly(self) -> None:
        result = _make("verify-crnt", "CRNT_TASK_TOMLS=")

        assert result.returncode != 0, "an empty CRNT_TASK_TOMLS was accepted"
        assert "crnt_task_tomls is empty" in _output(result).lower(), (
            f"empty CRNT_TASK_TOMLS must be named as the failure\n{_output(result)}"
        )

    def test_passes_on_seeded_good_task(self, tmp_path: Path) -> None:
        toml_path = tmp_path / "task.toml"
        toml_path.write_text(_GOOD_CRNT_TOML)

        result = _make("verify-crnt", f"CRNT_TASK_TOMLS={toml_path}")

        assert result.returncode == 0, _output(result)
        assert "[verify-crnt] checked 1 tasks" in _output(result)

    def test_fails_on_seeded_violation(self, tmp_path: Path) -> None:
        """repo-b has no required_files entry — CRNT must catch it and the
        target must propagate that as a nonzero exit, not swallow it.
        """
        toml_path = tmp_path / "task.toml"
        toml_path.write_text(_BAD_CRNT_TOML)

        result = _make("verify-crnt", f"CRNT_TASK_TOMLS={toml_path}")

        assert result.returncode != 0, (
            f"verify-crnt must fail on a task with an uncovered repo\n{_output(result)}"
        )
        assert "CRNT FAIL" in _output(result)


class TestVerifyExpectedSolutions:
    def test_reaches_the_real_corpus(self) -> None:
        """Regression guard: the old bare invocation errored at argparse
        before validating a single expected_solution.json.
        """
        result = _make("verify-expected-solutions")
        out = _output(result)

        assert "the following arguments are required" not in out.lower(), (
            f"verify-expected-solutions hit argparse's usage error instead of "
            f"running\n{out}"
        )
        assert "of 141 tasks)" in out or " tasks)" in out, (
            f"no per-task summary line — the walk never reached the corpus\n{out}"
        )
        assert "passed" in out and "0 tasks)" not in out, (
            f"reported walking zero tasks\n{out}"
        )

    def test_empty_tree_fails_loudly(self, tmp_path: Path) -> None:
        empty = tmp_path / "no_tasks_here"
        empty.mkdir()

        result = _make("verify-expected-solutions", f"EXPECTED_SOLUTIONS_PATH={empty}")

        assert result.returncode != 0, "an empty tree was accepted"
        assert "no task.toml under" in _output(result).lower(), (
            f"empty tree must be named as the failure\n{_output(result)}"
        )

    def test_fails_on_seeded_violation(self, tmp_path: Path) -> None:
        """expected_solution.json missing a checkpoint task.toml declares
        (C1 in validate_expected_solutions.py) must fail the gate.
        """
        task_dir = tmp_path / "seeded-task"
        task_dir.mkdir()
        (task_dir / "task.toml").write_text(
            textwrap.dedent(
                """\
                [task]
                id = "seeded-eval-bad"
                suite = "customer_escalation"

                [[checkpoints]]
                name = "check_one"
                weight = 1.0
                verifier = "checks/check_one.sh"
                """
            )
        )
        (task_dir / "expected_solution.json").write_text("{}")

        result = _make(
            "verify-expected-solutions", f"EXPECTED_SOLUTIONS_PATH={tmp_path}"
        )

        assert result.returncode != 0, (
            f"verify-expected-solutions must fail on a checkpoint with no "
            f"expected_solution.json entry\n{_output(result)}"
        )
        assert "FAIL seeded-eval-bad" in _output(result)
