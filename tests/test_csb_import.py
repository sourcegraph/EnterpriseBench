"""Contract tests for the CSB -> EB task converter.

The converter's job is a schema translation, and its two failure modes are both
silent: a task that scores nothing because the scorer never ran, and an MCP arm
that searches a repository name Sourcegraph has never indexed. Both are caught
here rather than after a paid sweep.
"""

from __future__ import annotations

import json
import subprocess
import sys
import tomllib
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
CONVERTER_DIR = REPO_ROOT / "experiments/csb_import"
CSB_ROOT = Path("/home/ds/projects/CodeScaleBench")

sys.path.insert(0, str(CONVERTER_DIR))
sys.path.insert(0, str(REPO_ROOT / "scripts/infra"))

pytestmark = pytest.mark.skipif(
    not (CSB_ROOT / "configs/runs/mirror_creation_manifest.json").exists(),
    reason="CodeScaleBench checkout not present",
)

SAMPLE = CSB_ROOT / "benchmarks/csb_org_compliance/ccx-compliance-053"


@pytest.fixture(scope="module")
def converted(tmp_path_factory) -> Path:
    from convert_csb_task import convert, load_mirror_index

    return convert(SAMPLE, tmp_path_factory.mktemp("csb_out"), load_mirror_index())


def test_converted_task_matches_the_eb_schema(converted: Path) -> None:
    jsonschema = pytest.importorskip("jsonschema")
    schema = json.loads((REPO_ROOT / "schemas/task.schema.json").read_text())
    with (converted / "task.toml").open("rb") as handle:
        jsonschema.validate(tomllib.load(handle), schema)


def test_rev_round_trips_to_the_indexed_mirror_name(converted: Path) -> None:
    """EB re-derives the mirror name from url+rev.

    If it derives a name Sourcegraph has not indexed, the MCP arms search an
    empty repo and return nothing — a run that looks valid and measures a
    missing index rather than the agent.
    """
    from mirror_naming import derive_mirror_name

    with (converted / "task.toml").open("rb") as handle:
        data = tomllib.load(handle)
    repo = data["repos"][0]
    assert derive_mirror_name(repo["url"], repo["rev"]) == data["metadata"]["csb_mirror"]


def test_the_csb_oracle_answer_still_scores_full_marks(converted: Path, tmp_path) -> None:
    """The expected-solution gate: CSB's own oracle must score 1.0 through the
    generated checkpoint, or the scoring translation lost something."""
    workspace = tmp_path / "ws"
    workspace.mkdir()
    (workspace / "answer.json").write_text(
        (SAMPLE / "tests/oracle_answer.json").read_text()
    )
    proc = subprocess.run(
        ["bash", str(converted / "checks/check_csb_oracle.sh")],
        capture_output=True, text=True, timeout=300,
        env={"PATH": "/usr/bin:/bin", "WORKSPACE": str(workspace)},
    )
    verdict = json.loads(proc.stdout.strip().splitlines()[-1])
    assert verdict["score"] == pytest.approx(1.0, abs=0.05)
    assert verdict["passed"] is True


def test_a_missing_answer_scores_zero_rather_than_erroring(converted: Path, tmp_path) -> None:
    workspace = tmp_path / "empty"
    workspace.mkdir()
    proc = subprocess.run(
        ["bash", str(converted / "checks/check_csb_oracle.sh")],
        capture_output=True, text=True, timeout=60,
        env={"PATH": "/usr/bin:/bin", "WORKSPACE": str(workspace)},
    )
    assert proc.returncode == 0
    verdict = json.loads(proc.stdout.strip().splitlines()[-1])
    assert verdict["score"] == 0.0
    assert "answer.json not found" in verdict["detail"]


def test_the_mount_prefix_does_not_change_the_score(converted: Path, tmp_path) -> None:
    """An identical file set must score identically however the agent spelled it.

    Agents reading code locally cite "clients/src/...", agents reaching it
    through remote search cite "/workspace/kafka/clients/src/...". CSB's oracle
    predates EB's sandbox and compares the raw string, so without normalisation
    the mount prefix alone drove file_set_match from 0.80 to 0.00 and made the
    MCP arms look like a retrieval collapse they were not.
    """
    oracle = json.loads((SAMPLE / "tests/oracle_answer.json").read_text())
    prefixed = json.loads(json.dumps(oracle))
    for entry in prefixed.get("files", []):
        if isinstance(entry, dict) and "path" in entry:
            entry["path"] = f"/workspace/kafka/{entry['path']}"

    scores = []
    for name, payload in (("plain", oracle), ("prefixed", prefixed)):
        workspace = tmp_path / name
        workspace.mkdir()
        (workspace / "answer.json").write_text(json.dumps(payload))
        proc = subprocess.run(
            ["bash", str(converted / "checks/check_csb_oracle.sh")],
            capture_output=True, text=True, timeout=300,
            env={"PATH": "/usr/bin:/bin", "WORKSPACE": str(workspace)},
        )
        scores.append(json.loads(proc.stdout.strip().splitlines()[-1])["score"])

    assert scores[0] == pytest.approx(scores[1], abs=0.01)


def test_a_malformed_answer_scores_zero_rather_than_erroring(converted: Path, tmp_path) -> None:
    workspace = tmp_path / "bad"
    workspace.mkdir()
    (workspace / "answer.json").write_text("{not json")
    proc = subprocess.run(
        ["bash", str(converted / "checks/check_csb_oracle.sh")],
        capture_output=True, text=True, timeout=60,
        env={"PATH": "/usr/bin:/bin", "WORKSPACE": str(workspace)},
    )
    assert proc.returncode == 0
    verdict = json.loads(proc.stdout.strip().splitlines()[-1])
    assert verdict["score"] == 0.0
    assert "not readable JSON" in verdict["detail"]


def test_an_unmapped_mirror_is_refused_rather_than_guessed() -> None:
    from convert_csb_task import ConversionError, convert

    with pytest.raises(ConversionError, match="absent from the creation manifest"):
        convert(SAMPLE, Path("/tmp"), {})
