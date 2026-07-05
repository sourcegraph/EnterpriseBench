"""Tests for scripts/infra/generate_mcp_instructions.py."""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(REPO_ROOT / "scripts" / "infra"))

from generate_mcp_instructions import generate_instruction_mcp  # noqa: E402

VALID_TASK_DATA = {
    "task": {"id": "test-task-001", "description": "Example task"},
    "metadata": {},
    "repos": [
        {
            "url": "https://github.com/psf/requests",
            "rev": "abc12345",
            "path": "requests",
            "role": "primary",
        }
    ],
}


def _task_with_repo(repo: dict) -> dict:
    return {
        "task": {"id": "test-task-002", "description": "Example task"},
        "metadata": {},
        "repos": [repo],
    }


def test_generate_instruction_mcp_valid_repo() -> None:
    content = generate_instruction_mcp(VALID_TASK_DATA)
    assert "requests" in content


def test_generate_instruction_mcp_rejects_path_traversal_url() -> None:
    repo = {
        "url": "https://github.com/org/../../etc/passwd",
        "rev": "abc12345",
        "path": "repo",
        "role": "primary",
    }
    with pytest.raises(ValueError, match="Invalid repo URL"):
        generate_instruction_mcp(_task_with_repo(repo))


def test_generate_instruction_mcp_rejects_path_traversal_rev() -> None:
    repo = {
        "url": "https://github.com/org/repo",
        "rev": "../../etc/passwd",
        "path": "repo",
        "role": "primary",
    }
    with pytest.raises(ValueError, match="Invalid repo rev"):
        generate_instruction_mcp(_task_with_repo(repo))


def test_generate_instruction_mcp_rejects_path_traversal_in_multi_repo_pass() -> None:
    """Two repos so the second (multi-repo OR-pattern) loop is exercised too."""
    repos = [
        {
            "url": "https://github.com/psf/requests",
            "rev": "abc12345",
            "path": "requests",
            "role": "primary",
        },
        {
            "url": "https://github.com/org/../../etc/passwd",
            "rev": "abc12345",
            "path": "repo2",
            "role": "secondary",
        },
    ]
    task_data = {
        "task": {"id": "test-task-003", "description": "Multi-repo task"},
        "metadata": {},
        "repos": repos,
    }
    with pytest.raises(ValueError, match="Invalid repo URL"):
        generate_instruction_mcp(task_data)
