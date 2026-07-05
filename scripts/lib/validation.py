"""Shared input validation for EnterpriseBench scripts."""

import re


def validate_repo_entry(repo: dict) -> None:
    """Validate a repo entry parsed from task.toml.

    Raises ValueError if any field contains shell metacharacters or path
    traversal sequences. Must be called before interpolating fields into
    Dockerfiles or subprocess arguments.
    """
    url = repo.get("url", "")
    rev = repo.get("rev", "")
    path = repo.get("path", "")

    if not re.match(r'^https?://[a-zA-Z0-9._/-]+$', url) or '..' in url:
        raise ValueError(f"Invalid repo URL (rejected due to unsafe characters or path traversal): {url!r}")

    if not re.match(r'^[a-zA-Z0-9._/-]+$', rev) or '..' in rev:
        raise ValueError(f"Invalid repo rev (rejected due to unsafe characters or path traversal): {rev!r}")

    if not re.match(r'^[a-zA-Z0-9][a-zA-Z0-9_.-]*$', path):
        raise ValueError(f"Invalid repo path (no slashes, no .., must start with alnum): {path!r}")
