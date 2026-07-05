"""Tests for scripts/lib/validation.py::validate_repo_entry."""

import os
import sys

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "scripts", "lib"))

from validation import validate_repo_entry  # noqa: E402


def _repo(url="https://github.com/org/repo", rev="v1.0.0", path="repo") -> dict:
    return {"url": url, "rev": rev, "path": path}


class TestValidateRepoEntry:
    def test_accepts_valid_entry(self) -> None:
        validate_repo_entry(_repo())

    def test_rejects_url_with_shell_metacharacters(self) -> None:
        with pytest.raises(ValueError, match="Invalid repo URL"):
            validate_repo_entry(_repo(url="https://github.com/org/repo; rm -rf /"))

    def test_rejects_url_with_path_traversal(self) -> None:
        # The character class alone (letters, digits, ._/-) does not exclude
        # ".." segments -- a url like this collapses client-side (curl/git
        # normalize dot-segments per RFC 3986 S5.3) to an attacker-chosen
        # org/repo, redirecting mirror creation away from what the task.toml
        # appears to reference.
        with pytest.raises(ValueError, match="Invalid repo URL"):
            validate_repo_entry(
                _repo(url="https://github.com/legit-org/legit-repo/../../other-org/secret-repo")
            )

    def test_rejects_rev_with_path_traversal(self) -> None:
        with pytest.raises(ValueError, match="Invalid repo rev"):
            validate_repo_entry(_repo(rev="../../etc/passwd"))

    def test_rejects_path_with_traversal(self) -> None:
        with pytest.raises(ValueError, match="Invalid repo path"):
            validate_repo_entry(_repo(path="../etc"))

    def test_rejects_empty_path(self) -> None:
        with pytest.raises(ValueError, match="Invalid repo path"):
            validate_repo_entry(_repo(path=""))
