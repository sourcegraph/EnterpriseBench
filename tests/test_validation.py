"""Tests for scripts/lib/validation.py::validate_repo_entry."""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(REPO_ROOT / "scripts" / "lib"))

from validation import validate_repo_entry  # noqa: E402


def _repo(url: str = "https://github.com/org/repo", rev: str = "abc12345", path: str = "repo") -> dict:
    return {"url": url, "rev": rev, "path": path}


def test_valid_repo_entry_passes() -> None:
    validate_repo_entry(_repo())


def test_url_with_path_traversal_rejected() -> None:
    with pytest.raises(ValueError, match="Invalid repo URL"):
        validate_repo_entry(_repo(url="https://github.com/org/../../etc/passwd"))


def test_url_with_shell_metacharacters_rejected() -> None:
    with pytest.raises(ValueError, match="Invalid repo URL"):
        validate_repo_entry(_repo(url="https://github.com/org/repo; rm -rf /"))


def test_rev_with_path_traversal_rejected() -> None:
    with pytest.raises(ValueError, match="Invalid repo rev"):
        validate_repo_entry(_repo(rev="../../etc/passwd"))


def test_path_with_traversal_rejected() -> None:
    with pytest.raises(ValueError, match="Invalid repo path"):
        validate_repo_entry(_repo(path="../etc"))
