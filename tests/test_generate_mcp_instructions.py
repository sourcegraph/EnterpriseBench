"""Tests for scripts/infra/generate_mcp_instructions.py."""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(REPO_ROOT / "scripts" / "infra"))

from generate_mcp_instructions import generate_instruction_mcp  # noqa: E402

def _repo(url: str = "https://github.com/psf/requests", rev: str = "abc12345", path: str = "requests", role: str = "primary") -> dict:
    return {"url": url, "rev": rev, "path": path, "role": role}


def _task_with_repos(repos: list[dict], task_id: str = "test-task") -> dict:
    return {
        "task": {"id": task_id, "description": "Example task"},
        "metadata": {},
        "repos": repos,
    }


def test_generate_instruction_mcp_valid_repo() -> None:
    content = generate_instruction_mcp(_task_with_repos([_repo()]))
    assert "requests" in content


def test_generate_instruction_mcp_rejects_path_traversal_url() -> None:
    repo = _repo(url="https://github.com/org/../../etc/passwd")
    with pytest.raises(ValueError, match="Invalid repo URL"):
        generate_instruction_mcp(_task_with_repos([repo]))


def test_generate_instruction_mcp_rejects_path_traversal_rev() -> None:
    repo = _repo(rev="../../etc/passwd")
    with pytest.raises(ValueError, match="Invalid repo rev"):
        generate_instruction_mcp(_task_with_repos([repo]))


def test_generate_instruction_mcp_rejects_path_traversal_in_multi_repo_pass() -> None:
    """A multi-repo task (>1 repos, triggers the OR-pattern section) still
    validates every repo entry, not just the first."""
    repos = [
        _repo(),
        _repo(url="https://github.com/org/../../etc/passwd", path="repo2", role="secondary"),
    ]
    with pytest.raises(ValueError, match="Invalid repo URL"):
        generate_instruction_mcp(_task_with_repos(repos))
