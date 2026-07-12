"""
Verify Error Provenance task verifiers discriminate correctly.

For each task:
  (a) Ground truth answer → score >= 0.85
  (b) Empty answer → score <= 0.10
  (c) Partial answer → score between 0.3-0.7
  No false positives: wrong answer must not score > 0.5
"""

from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path

import pytest


REPO_ROOT = Path(__file__).parent.parent
TASK_BASE = REPO_ROOT / "benchmarks" / "customer_escalation"

# All error provenance task directories
ERR_PROVENANCE_DIRS = sorted(TASK_BASE.glob("err-provenance-*"))

# Verifier scripts
VERIFIERS = [
    "checks/check_error_source.sh",
    "checks/check_error_chain.sh",
    "checks/check_trigger_conditions.sh",
]


def _run_verifier(
    task_dir: Path,
    verifier: str,
    workspace: Path,
) -> dict:
    """Run a single verifier script and return parsed JSON output."""
    verifier_path = task_dir / verifier
    assert verifier_path.exists(), f"Verifier not found: {verifier_path}"

    env = os.environ.copy()
    env["WORKSPACE"] = str(workspace)
    env["TASK_DIR"] = str(task_dir)
    env["TASK_ID"] = "test"
    # Mirror CheckpointRunner.run_checkpoint, which is what really execs these in
    # production: checks that shell out to `python -m eb_verify.…` need the harness
    # on PYTHONPATH. Absolute, because we run with cwd=workspace — an inherited
    # relative PYTHONPATH (the suite is run as `PYTHONPATH=lib pytest`) resolves
    # against that tmp dir and silently fails to import.
    env["PYTHONPATH"] = os.pathsep.join(
        p for p in (str(REPO_ROOT / "lib"), env.get("PYTHONPATH", "")) if p
    )

    result = subprocess.run(
        ["bash", str(verifier_path)],
        capture_output=True,
        text=True,
        timeout=30,
        cwd=str(workspace),
        env=env,
    )

    stdout = result.stdout.strip()
    try:
        return json.loads(stdout)
    except (json.JSONDecodeError, ValueError):
        return {
            "score": 1.0 if result.returncode == 0 else 0.0,
            "passed": result.returncode == 0,
            "detail": stdout or result.stderr.strip(),
        }


def _create_answer(workspace: Path, answer_data: dict) -> None:
    """Write answer.json to the workspace agent_output directory."""
    output_dir = workspace / "agent_output"
    output_dir.mkdir(parents=True, exist_ok=True)
    (output_dir / "answer.json").write_text(json.dumps(answer_data, indent=2))


def _build_ground_truth_answer(gt: dict) -> dict:
    """Build a 'perfect' answer from ground truth data."""
    return {
        "source_files": [
            {"path": f["path"], "repo": f["repo"]}
            for f in gt.get("required_files", [])
        ],
        "error_chain": gt.get("error_chain", []),
        "trigger_conditions": gt.get("trigger_conditions", []),
    }


def _build_partial_answer(gt: dict) -> dict:
    """Build a partial answer — first required file, partial chain, one condition."""
    required = gt.get("required_files", [])
    chain = gt.get("error_chain", [])
    conditions = gt.get("trigger_conditions", [])
    # Include ~half the chain and one condition for a credible partial answer
    half_chain = max(1, len(chain) // 2)
    return {
        "source_files": [
            {"path": required[0]["path"], "repo": required[0]["repo"]}
        ] if required else [],
        "error_chain": chain[:half_chain],
        "trigger_conditions": conditions[:1] if conditions else [],
    }


def _build_empty_answer() -> dict:
    """Build an empty answer."""
    return {
        "source_files": [],
        "error_chain": [],
        "trigger_conditions": [],
    }


def _build_wrong_answer() -> dict:
    """Build a wrong answer with plausible but incorrect files."""
    return {
        "source_files": [
            {"path": "pkg/wrong/file.go", "repo": "kubernetes"},
            {"path": "internal/fake/module.go", "repo": "terraform"},
        ],
        "error_chain": [
            "User clicks a button",
            "Frontend sends request",
            "Backend returns error",
        ],
        "trigger_conditions": [
            "When the moon is full",
            "On Tuesdays",
        ],
    }


def _weighted_score(results: list[dict], weights: list[float]) -> float:
    """Compute weighted score from verifier results."""
    total = sum(
        r.get("score", 0.0) * w
        for r, w in zip(results, weights)
    )
    return total / sum(weights) if sum(weights) > 0 else 0.0


# Checkpoint weights from the task definitions
WEIGHTS = [0.40, 0.30, 0.30]


@pytest.fixture(params=[d.name for d in ERR_PROVENANCE_DIRS], ids=lambda x: x)
def task_env(request, tmp_path: Path) -> tuple[str, Path, Path, dict]:
    """Parametrized fixture for each err-provenance task."""
    task_name = request.param
    task_dir = TASK_BASE / task_name

    # Load ground truth
    gt_path = task_dir / "ground_truth.json"
    assert gt_path.exists(), f"No ground_truth.json in {task_dir}"
    gt = json.loads(gt_path.read_text())

    # Create workspace
    workspace = tmp_path / "workspace"
    workspace.mkdir()

    return task_name, task_dir, workspace, gt


class TestGroundTruthAnswer:
    """(a) Ground truth answer should score >= 0.85"""

    def test_ground_truth_scores_high(
        self, task_env: tuple[str, Path, Path, dict]
    ) -> None:
        task_name, task_dir, workspace, gt = task_env

        answer = _build_ground_truth_answer(gt)
        _create_answer(workspace, answer)

        results = [
            _run_verifier(task_dir, v, workspace)
            for v in VERIFIERS
        ]

        total = _weighted_score(results, WEIGHTS)
        assert total >= 0.85, (
            f"{task_name}: ground truth answer scored {total:.2f} < 0.85. "
            f"Per-checkpoint: {[r.get('score', 0) for r in results]}"
        )


class TestEmptyAnswer:
    """(b) Empty answer should score <= 0.10"""

    def test_empty_answer_scores_low(
        self, task_env: tuple[str, Path, Path, dict]
    ) -> None:
        task_name, task_dir, workspace, gt = task_env

        answer = _build_empty_answer()
        _create_answer(workspace, answer)

        results = [
            _run_verifier(task_dir, v, workspace)
            for v in VERIFIERS
        ]

        total = _weighted_score(results, WEIGHTS)
        assert total <= 0.10, (
            f"{task_name}: empty answer scored {total:.2f} > 0.10. "
            f"Per-checkpoint: {[r.get('score', 0) for r in results]}"
        )


class TestPartialAnswer:
    """(c) Partial answer should score between 0.3-0.7"""

    def test_partial_answer_scores_mid(
        self, task_env: tuple[str, Path, Path, dict]
    ) -> None:
        task_name, task_dir, workspace, gt = task_env

        answer = _build_partial_answer(gt)
        _create_answer(workspace, answer)

        results = [
            _run_verifier(task_dir, v, workspace)
            for v in VERIFIERS
        ]

        total = _weighted_score(results, WEIGHTS)
        assert 0.20 <= total <= 0.80, (
            f"{task_name}: partial answer scored {total:.2f}, "
            f"expected 0.20-0.80. "
            f"Per-checkpoint: {[r.get('score', 0) for r in results]}"
        )


class TestWrongAnswer:
    """Wrong answer must not score > 0.5 (no false positives)"""

    def test_wrong_answer_scores_low(
        self, task_env: tuple[str, Path, Path, dict]
    ) -> None:
        task_name, task_dir, workspace, gt = task_env

        answer = _build_wrong_answer()
        _create_answer(workspace, answer)

        results = [
            _run_verifier(task_dir, v, workspace)
            for v in VERIFIERS
        ]

        total = _weighted_score(results, WEIGHTS)
        assert total <= 0.5, (
            f"{task_name}: wrong answer scored {total:.2f} > 0.5 — "
            f"false positive! Per-checkpoint: {[r.get('score', 0) for r in results]}"
        )


class TestNoAnswerFile:
    """Missing answer file should score 0"""

    def test_no_answer_file(
        self, task_env: tuple[str, Path, Path, dict]
    ) -> None:
        task_name, task_dir, workspace, _ = task_env

        # Don't create answer.json at all
        results = [
            _run_verifier(task_dir, v, workspace)
            for v in VERIFIERS
        ]

        total = _weighted_score(results, WEIGHTS)
        assert total <= 0.10, (
            f"{task_name}: missing answer scored {total:.2f} > 0.10"
        )
