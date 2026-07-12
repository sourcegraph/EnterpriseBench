"""Shared pytest fixtures for eb_verify tests."""

from __future__ import annotations

import os
import sys
from pathlib import Path

import pytest

# Make the repo root and lib/ importable for the whole session, explicitly and
# in one place. Previously the suite relied on `PYTHONPATH=lib` being set by the
# caller (tests/eb_metrics/ simply failed to collect without it), and once
# stratum_selector.py started inserting these paths at import time, whether a
# test module could import `eb_metrics` depended on pytest's collection ORDER.
# Test outcomes must not depend on what happened to be imported first.
_REPO_ROOT = Path(__file__).resolve().parent.parent
for _p in (_REPO_ROOT, _REPO_ROOT / "lib"):
    if str(_p) not in sys.path:
        sys.path.insert(0, str(_p))


def pytest_configure(config):
    config.addinivalue_line("markers", "network: requires network access (git ls-remote)")
    config.addinivalue_line("markers", "docker: requires Docker daemon")


FIXTURES_DIR = Path(__file__).parent / "fixtures"
REPO_ROOT = Path(__file__).parent.parent


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
