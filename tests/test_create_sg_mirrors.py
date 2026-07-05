"""Tests for scripts/infra/create_sg_mirrors.py::extract_mirrors_from_task.

Verifies:
  - Valid repo entries produce the expected mirror dict shape.
  - Repo entries with unsafe url/rev/path characters are rejected via
    validate_repo_entry() rather than silently flowing into create_mirror()
    -> resolve_commit()'s `gh api repos/{org}/commits/{ref}` call.
"""

import os
import sys
from pathlib import Path

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "scripts", "infra"))

from create_sg_mirrors import extract_mirrors_from_task  # noqa: E402


def _task(repos: list) -> dict:
    return {"task": {"id": "test-001"}, "repos": repos}


class TestExtractMirrorsFromTask:
    def test_valid_repo_produces_expected_mirror(self) -> None:
        task_data = _task(
            [
                {
                    "url": "https://github.com/grpc/grpc-go",
                    "rev": "v1.60.0",
                    "path": "grpc-go",
                }
            ]
        )
        mirrors = extract_mirrors_from_task(task_data, Path("fake.toml"))
        assert mirrors == [
            {
                "upstream": "github.com/grpc/grpc-go",
                "commit": "v1.60.0",
                "mirror": "sg-evals/grpc-go--v1.60.0",
                "pin_source": "task.toml repos[].rev",
                "tasks": ["test-001"],
            }
        ]

    def test_skips_repo_missing_url_or_rev(self) -> None:
        task_data = _task([{"url": "", "rev": "v1.0", "path": "repo"}])
        assert extract_mirrors_from_task(task_data, Path("fake.toml")) == []

    def test_rejects_rev_with_path_traversal(self) -> None:
        # A crafted rev like this would otherwise flow unvalidated into
        # create_mirror() -> resolve_commit()'s `gh api repos/{org}/commits/{ref}` call.
        task_data = _task(
            [
                {
                    "url": "https://github.com/org/repo",
                    "rev": "../../etc/passwd",
                    "path": "repo",
                }
            ]
        )
        with pytest.raises(ValueError, match="Invalid repo rev"):
            extract_mirrors_from_task(task_data, Path("fake.toml"))

    def test_rejects_url_with_unsafe_characters(self) -> None:
        task_data = _task(
            [
                {
                    "url": "https://github.com/org/repo; rm -rf /",
                    "rev": "v1.0",
                    "path": "repo",
                }
            ]
        )
        with pytest.raises(ValueError, match="Invalid repo URL"):
            extract_mirrors_from_task(task_data, Path("fake.toml"))
