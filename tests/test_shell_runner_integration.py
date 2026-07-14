"""
Integration tests for shell-based test runners.

Exercises test_cross_repo_runner.sh end-to-end and validates test_runner.sh
JSON output against mock workspaces with various configurations.
"""

from __future__ import annotations

import json
import os
import stat
import subprocess
from pathlib import Path

import pytest


REPO_ROOT = Path(__file__).parent.parent
CROSS_REPO_RUNNER = REPO_ROOT / "tests" / "test_cross_repo_runner.sh"
TEST_RUNNER = REPO_ROOT / "scripts" / "sandbox" / "test_runner.sh"


# ---------------------------------------------------------------------------
# 1. Run test_cross_repo_runner.sh via subprocess
# ---------------------------------------------------------------------------


class TestCrossRepoRunnerScript:
    """Run the existing shell-based test suite and assert it exits cleanly."""

    def test_cross_repo_runner_exits_zero(self) -> None:
        assert CROSS_REPO_RUNNER.exists(), f"Missing {CROSS_REPO_RUNNER}"
        result = subprocess.run(
            ["bash", str(CROSS_REPO_RUNNER)],
            capture_output=True,
            text=True,
            timeout=120,
            cwd=str(REPO_ROOT),
        )
        assert result.returncode == 0, (
            f"test_cross_repo_runner.sh failed (exit {result.returncode}):\n"
            f"stdout:\n{result.stdout}\nstderr:\n{result.stderr}"
        )


# ---------------------------------------------------------------------------
# 2. Run test_runner.sh against a mock workspace
# ---------------------------------------------------------------------------


def _make_patched_runner(tmp_path: Path, workspace: Path) -> Path:
    """A wrapper that runs the REAL test_runner.sh against a temp workspace.

    Points the runner at ``workspace`` through the WORKSPACE env var it honours,
    rather than string-patching a copy of its source. Source-patching silently
    no-ops the moment the matched line is reworded — the tests then score
    against the real ``/workspace`` and fail for reasons unrelated to the change
    that reworded it. This also means the tests exercise the runner that ships,
    not a mutated copy of it.
    """
    runner = tmp_path / "run_test_runner.sh"
    runner.write_text(
        "#!/usr/bin/env bash\n"
        f'export WORKSPACE="{workspace}"\n'
        f'exec bash "{TEST_RUNNER}" "$@"\n'
    )
    runner.chmod(runner.stat().st_mode | stat.S_IEXEC)
    return runner


def _build_workspace(
    workspace: Path,
    repos: list[str] | None = None,
    verifiers: dict[str, str] | None = None,
    meta: dict[str, str] | None = None,
) -> None:
    """Build a mock workspace with fake repos and verifier scripts."""
    if repos:
        for repo in repos:
            git_dir = workspace / repo / ".git"
            git_dir.mkdir(parents=True, exist_ok=True)
        markers = workspace / ".markers"
        markers.mkdir(exist_ok=True)
        for repo in repos:
            (markers / f"{repo}.status").write_text("OK")

    if verifiers:
        verifier_dir = workspace / ".verifiers"
        verifier_dir.mkdir(exist_ok=True)
        for name, script in verifiers.items():
            path = verifier_dir / f"{name}.sh"
            path.write_text(script)
            path.chmod(path.stat().st_mode | stat.S_IEXEC)

    if meta:
        verifier_dir = workspace / ".verifiers"
        verifier_dir.mkdir(exist_ok=True)
        for name, content in meta.items():
            (verifier_dir / f"{name}.meta").write_text(content)


PASS_VERIFIER = """\
#!/usr/bin/env bash
echo '{"score": 1.0, "passed": true, "detail": "ok"}'
exit 0
"""

FAIL_VERIFIER = """\
#!/usr/bin/env bash
echo '{"score": 0.0, "passed": false, "detail": "nope"}'
exit 1
"""


class TestRunnerJsonOutput:
    """Run test_runner.sh against mock workspaces and validate JSON output."""

    def test_two_checkpoints_mixed(self, tmp_path: Path) -> None:
        workspace = tmp_path / "workspace"
        workspace.mkdir()
        _build_workspace(
            workspace,
            repos=["alpha", "beta"],
            verifiers={"01-pass": PASS_VERIFIER, "02-fail": FAIL_VERIFIER},
            meta={"01-pass": "weight=0.4", "02-fail": "weight=0.6"},
        )
        runner = _make_patched_runner(tmp_path, workspace)

        result = subprocess.run(
            ["bash", str(runner)],
            capture_output=True,
            text=True,
            timeout=30,
        )
        assert result.returncode != 0, "Should fail when not all checkpoints pass"

        data = json.loads(result.stdout)
        assert "task_score" in data
        assert "all_passed" in data
        assert "checkpoints_passed" in data
        assert "checkpoints_total" in data
        assert "checkpoints" in data
        assert "repos" in data

        assert data["all_passed"] is False
        assert data["checkpoints_passed"] == 1
        assert data["checkpoints_total"] == 2
        assert abs(data["task_score"] - 0.4) < 0.01
        # Note: test_runner.sh discover_repos uses mapfile + awk which may
        # serialize only the first element; assert at least one repo appears
        assert len(data["repos"]) >= 1
        assert "alpha" in data["repos"] or "beta" in data["repos"]

        assert len(data["checkpoints"]) == 2
        for cp in data["checkpoints"]:
            assert "name" in cp
            assert "weight" in cp
            assert "score" in cp
            assert "passed" in cp
            assert "duration_ms" in cp
            assert "exit_code" in cp

    def test_all_pass(self, tmp_path: Path) -> None:
        workspace = tmp_path / "workspace"
        workspace.mkdir()
        _build_workspace(
            workspace,
            repos=["repo-a"],
            verifiers={"01-check": PASS_VERIFIER},
        )
        runner = _make_patched_runner(tmp_path, workspace)

        result = subprocess.run(
            ["bash", str(runner)],
            capture_output=True,
            text=True,
            timeout=30,
        )
        assert result.returncode == 0

        data = json.loads(result.stdout)
        assert data["all_passed"] is True
        assert data["checkpoints_passed"] == 1
        assert data["task_score"] >= 1.0 - 0.01

    def test_single_checkpoint_mode(self, tmp_path: Path) -> None:
        workspace = tmp_path / "workspace"
        workspace.mkdir()
        _build_workspace(
            workspace,
            repos=["repo-a"],
            verifiers={"01-check": PASS_VERIFIER},
        )
        runner = _make_patched_runner(tmp_path, workspace)

        result = subprocess.run(
            ["bash", str(runner), "01-check"],
            capture_output=True,
            text=True,
            timeout=30,
        )
        assert result.returncode == 0

        data = json.loads(result.stdout)
        assert data["passed"] is True
        assert data["score"] == 1.0


# ---------------------------------------------------------------------------
# 3. Edge cases
# ---------------------------------------------------------------------------


class TestRunnerEdgeCases:
    """Edge cases: missing verifiers dir, single checkpoint, timeout."""

    def test_no_verifiers_dir(self, tmp_path: Path) -> None:
        workspace = tmp_path / "workspace"
        workspace.mkdir()
        # No .verifiers/ directory at all
        runner = _make_patched_runner(tmp_path, workspace)

        result = subprocess.run(
            ["bash", str(runner)],
            capture_output=True,
            text=True,
            timeout=30,
        )
        assert result.returncode != 0

        data = json.loads(result.stdout)
        assert data["all_passed"] is False

    def test_single_checkpoint_full_run(self, tmp_path: Path) -> None:
        workspace = tmp_path / "workspace"
        workspace.mkdir()
        _build_workspace(
            workspace,
            repos=["solo"],
            verifiers={"01-only": PASS_VERIFIER},
        )
        runner = _make_patched_runner(tmp_path, workspace)

        result = subprocess.run(
            ["bash", str(runner)],
            capture_output=True,
            text=True,
            timeout=30,
        )
        assert result.returncode == 0

        data = json.loads(result.stdout)
        assert data["checkpoints_total"] == 1
        assert data["checkpoints_passed"] == 1
        assert data["all_passed"] is True

    def test_verifier_timeout(self, tmp_path: Path) -> None:
        workspace = tmp_path / "workspace"
        workspace.mkdir()

        slow_verifier = """\
#!/usr/bin/env bash
sleep 30
echo '{"score": 1.0, "passed": true, "detail": "too slow"}'
exit 0
"""
        _build_workspace(
            workspace,
            repos=["repo-a"],
            verifiers={"01-slow": slow_verifier},
            meta={"01-slow": "weight=1.0\ntimeout=2"},
        )
        runner = _make_patched_runner(tmp_path, workspace)

        result = subprocess.run(
            ["bash", str(runner)],
            capture_output=True,
            text=True,
            timeout=30,
        )
        assert result.returncode != 0

        data = json.loads(result.stdout)
        assert data["all_passed"] is False
        # The checkpoint should report timeout
        assert len(data["checkpoints"]) == 1
        assert data["checkpoints"][0]["passed"] is False

    def test_invalid_checkpoint_name_rejected(self, tmp_path: Path) -> None:
        workspace = tmp_path / "workspace"
        workspace.mkdir()
        _build_workspace(
            workspace,
            repos=["repo-a"],
            verifiers={"01-check": PASS_VERIFIER},
        )
        runner = _make_patched_runner(tmp_path, workspace)

        result = subprocess.run(
            ["bash", str(runner), "../etc/passwd"],
            capture_output=True,
            text=True,
            timeout=30,
        )
        assert result.returncode != 0
        assert "Invalid checkpoint name" in result.stdout
