"""Regression tests for bead EnterpriseBench-nzcv.

create_sg_mirrors.py's create_mirror() builds subprocess argv (git/gh/curl)
and archive URLs from repo url/rev derived from task.toml, but
extract_mirrors_from_task() built the manifest entry without calling
validate_repo_entry() first -- unlike run_task.py and dockerfile_generator.py,
which validate every repo entry immediately after parsing. All subprocess
calls in this file use list-form argv (no shell=True), so this is not a
live shell-injection path; the risk is unvalidated url/rev/path reaching
subprocess argv and archive-URL construction unchecked (path traversal,
malformed/attacker-influenced gh/curl invocations), which validate_repo_entry
closes off and which the parametrized cases below exercise.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(REPO_ROOT / "scripts" / "infra"))

from create_sg_mirrors import extract_mirrors_from_task, generate_manifest  # noqa: E402


def _task_data(repo: dict) -> dict:
    return {
        "task": {"id": "fake-task-001"},
        "repos": [repo],
    }


def test_valid_repo_produces_a_mirror() -> None:
    repo = {
        "url": "https://github.com/grpc/grpc-go",
        "rev": "v1.60.0",
        "path": "grpc-go",
        "role": "primary",
    }
    mirrors = extract_mirrors_from_task(_task_data(repo), Path("fake-task-001.toml"))
    assert len(mirrors) == 1
    assert mirrors[0]["upstream"] == "github.com/grpc/grpc-go"


@pytest.mark.parametrize(
    "field,value",
    [
        ("url", "https://github.com/org/repo; rm -rf /"),
        ("url", "https://github.com/org/$(touch pwned)"),
        ("url", "https://github.com/org/`touch pwned`"),
        ("rev", "v1.0.0; rm -rf /"),
        ("rev", "../../../etc/passwd"),
        ("path", "../../etc/passwd"),
        ("path", "/etc/passwd"),
    ],
)
def test_malicious_repo_field_rejected(field: str, value: str) -> None:
    repo = {
        "url": "https://github.com/grpc/grpc-go",
        "rev": "v1.60.0",
        "path": "grpc-go",
    }
    repo[field] = value
    with pytest.raises(ValueError):
        extract_mirrors_from_task(_task_data(repo), Path("fake-task-001.toml"))


def test_generate_manifest_rejects_malicious_task_file(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """generate_manifest() must not surface a mirror built from an unvalidated repo.

    extract_mirrors_from_task() raises on the malicious entry; generate_manifest()
    catches that ValueError specifically (distinct from ordinary TOML parse
    errors, which still get a WARNING and are skipped the same as before),
    records the file under "_rejected", and continues with the remaining task
    files rather than crashing the whole batch or silently emitting the unsafe
    mirror.
    """
    malicious = tmp_path / "malicious.toml"
    malicious.write_text(
        '[task]\nid = "malicious-001"\n\n'
        "[[repos]]\n"
        'url = "https://github.com/org/repo; rm -rf /"\n'
        'rev = "main"\n'
        'path = "repo"\n'
    )
    benign = tmp_path / "benign.toml"
    benign.write_text(
        '[task]\nid = "benign-001"\n\n'
        "[[repos]]\n"
        'url = "https://github.com/grpc/grpc-go"\n'
        'rev = "v1.60.0"\n'
        'path = "grpc-go"\n'
    )

    manifest = generate_manifest([malicious, benign])

    assert len(manifest["mirrors"]) == 1
    assert manifest["mirrors"][0]["upstream"] == "github.com/grpc/grpc-go"
    assert manifest["_rejected"] == [str(malicious)]

    captured = capsys.readouterr()
    assert "ERROR" in captured.err


def test_generate_manifest_still_warns_on_malformed_toml(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """A genuine TOML syntax error is a WARNING, not a security "_rejected" entry."""
    malformed = tmp_path / "malformed.toml"
    malformed.write_text("[[repos]\nurl = not valid toml")

    manifest = generate_manifest([malformed])

    assert manifest["mirrors"] == []
    assert manifest["_rejected"] == []

    captured = capsys.readouterr()
    assert "WARNING" in captured.err
