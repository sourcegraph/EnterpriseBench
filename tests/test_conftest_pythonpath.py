"""Regression test for EnterpriseBench-4sif: local lib/eb_verify must win over
a stale global editable install, regardless of test collection order."""

from __future__ import annotations

from pathlib import Path


def test_eb_verify_resolves_to_local_lib() -> None:
    import eb_verify

    repo_lib = (Path(__file__).parent.parent / "lib").resolve()
    resolved = Path(eb_verify.__file__).resolve()
    assert resolved.is_relative_to(repo_lib), (
        f"eb_verify resolved to {resolved}, expected it under {repo_lib}"
    )
