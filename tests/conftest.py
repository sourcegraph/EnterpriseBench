"""Shared pytest fixtures for eb_verify tests."""

from __future__ import annotations

import os
import sys
from pathlib import Path

import pytest


def pytest_configure(config):
    config.addinivalue_line("markers", "network: requires network access (git ls-remote)")
    config.addinivalue_line("markers", "docker: requires Docker daemon")


FIXTURES_DIR = Path(__file__).parent / "fixtures"
REPO_ROOT = Path(__file__).parent.parent
LIB_DIR = REPO_ROOT / "lib"

# Put this worktree's lib/ ahead of any globally installed eb_verify. A stale
# editable install (pip install -e lib run from a different clone) hardcodes
# an absolute path in its finder and silently shadows the local package,
# breaking collection for whichever test module happens to import eb_verify
# before another module's ad hoc sys.path.insert takes effect. Setting this
# here, in conftest, runs before any test module in this directory is
# collected, independent of file ordering or the global install's state.
if str(LIB_DIR) not in sys.path:
    sys.path.insert(0, str(LIB_DIR))


@pytest.fixture
def valid_task_path() -> Path:
    return FIXTURES_DIR / "valid_task.toml"


@pytest.fixture
def invalid_task_path() -> Path:
    return FIXTURES_DIR / "invalid_task.toml"


@pytest.fixture
def chain_task_path() -> Path:
    return FIXTURES_DIR / "chain_task.toml"


@pytest.fixture
def example_task_path() -> Path:
    return REPO_ROOT / "benchmarks" / "EXAMPLE_TASK.toml"


@pytest.fixture
def tmp_workspace(tmp_path: Path) -> Path:
    """Create a temp dir with a mock repo structure."""
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    # Create a mock repo
    repo = workspace / "repo"
    repo.mkdir()
    (repo / ".git").mkdir()
    (repo / "src").mkdir()
    (repo / "src" / "main.py").write_text("print('hello')\n")
    return workspace
