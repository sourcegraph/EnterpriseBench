"""The canonical `make test` target must actually execute the suite.

Regression guard for a target that pointed at a directory holding no tests, so
`make test` collected nothing on every invocation. A command that runs zero
tests must never be mistakable for a passing suite.

Every `make test` below is either `--collect-only` or aimed at a path with no
tests in it. None of them executes the real suite, which contains this file.
"""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent

pytestmark = pytest.mark.skipif(
    shutil.which("make") is None, reason="make is not installed"
)


def _make(*args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["make", *args],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        timeout=60,
    )


def _output(result: subprocess.CompletedProcess[str]) -> str:
    return f"{result.stdout[-2000:]}{result.stderr[-2000:]}"


def test_make_test_collects_at_least_one_test() -> None:
    """`make test` must reach tests. Collect them, but do not run them.

    Overriding only PYTEST_ARGS exercises the real recipe — its paths, its
    PYTHONPATH, its zero-collection guard. pytest exits 5 when it collects
    nothing, which the target turns into a failure, so a clean exit here is
    itself the proof that the configured paths hold tests.
    """
    result = _make("test", "PYTEST_ARGS=-q --collect-only")

    assert result.returncode == 0, (
        f"`make test` collected no tests, or could not collect at all\n{_output(result)}"
    )


def test_zero_collection_fails_loudly(tmp_path: Path) -> None:
    """Pointed at a directory with no tests, `make test` must fail and say so."""
    empty = tmp_path / "no_tests_here"
    empty.mkdir()

    result = _make("test", f"PYTEST_PATHS={empty}")

    assert result.returncode != 0, "a run that collected 0 tests reported success"
    assert "collected 0 tests" in (result.stdout + result.stderr).lower(), (
        "zero-collection must be reported explicitly, not as a bare `Error 5`\n"
        f"{_output(result)}"
    )


def test_empty_test_paths_is_rejected() -> None:
    """`make test PYTEST_PATHS=` must refuse to run rather than scan the repo.

    With no path arguments pytest walks the rootdir, which pulls in the whole
    repository (this file included) — so the target must reject empty input
    instead of launching a recursive full-tree collection.
    """
    result = _make("test", "PYTEST_PATHS=")

    assert result.returncode != 0, "empty PYTEST_PATHS was accepted"
    assert "pytest_paths is empty" in (result.stdout + result.stderr).lower(), (
        f"an empty PYTEST_PATHS must be named as the failure\n{_output(result)}"
    )
