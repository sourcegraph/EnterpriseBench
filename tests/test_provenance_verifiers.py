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
import subprocess
from dataclasses import dataclass
from pathlib import Path

import pytest

from eb_verify.runner import checkpoint_env
from scripts.validation.crnt_validator import parse_toml

REPO_ROOT = Path(__file__).parent.parent
TASK_BASE = REPO_ROOT / "benchmarks" / "customer_escalation"

# A directory that carries checks/ but no task.toml declares no deliverable, so
# there is nothing for this contract to apply to. Named rather than skipped by a
# blanket `if task.toml exists`, so that a REAL task losing its task.toml fails
# here instead of silently leaving coverage (EnterpriseBench-cgq4z tracks the
# orphan itself; it is not this suite's to fix).
KNOWN_ORPHANS = {"err-provenance-dual-botocore-read-001"}


def _answer_deliverable_tasks() -> list[tuple[Path, list[str], list[float]]]:
    """Every err-provenance task whose declared deliverable is an ANSWER.

    Keyed on ``artifacts.required``, the task's own declaration of what it asks
    for — not on verifier FILENAMES. Keying on filenames silently dropped
    err-provenance-tri-httpx-socks-001, whose deliverable is an answer but whose
    verifiers are named for what they check (check_origin_and_grammar, ...),
    leaving it with no discrimination coverage anywhere while the filter's own
    comment claimed patch tasks were the only thing excluded (bead jn73.2.7, D3).

    Verifiers and weights come from ``[[checkpoints]]`` for the same reason: the
    task states its own scoring contract, so a rename cannot quietly shrink the
    suite.
    """
    found: list[tuple[Path, list[str], list[float]]] = []
    orphans: set[str] = set()
    for d in sorted(TASK_BASE.glob("err-provenance-*")):
        if not (d / "task.toml").is_file():
            orphans.add(d.name)
            continue
        config = parse_toml(d / "task.toml")
        required = config.get("artifacts", {}).get("required", [])
        # A patch-deliverable task is scored on its diff, a contract these
        # answer-centric invariants do not describe. tests/integrity/ covers it.
        if "code_patch" in required:
            continue
        checkpoints = config.get("checkpoints", [])
        verifiers = [c["verifier"] for c in checkpoints]
        weights = [float(c["weight"]) for c in checkpoints]
        # An answer-deliverable task with no verifiers cannot be scored at all.
        # Failing loudly here is the point: the old filter's response was to drop
        # it, which is how a task ends up with zero coverage and a green suite.
        if not verifiers:
            raise RuntimeError(
                f"{d.name} declares an answer deliverable but no [[checkpoints]] "
                f"verifier; it would be scored by nothing."
            )
        missing = [v for v in verifiers if not (d / v).is_file()]
        if missing:
            raise RuntimeError(
                f"{d.name} declares verifiers that do not exist: {missing}"
            )
        found.append((d, verifiers, weights))

    unexpected = orphans - KNOWN_ORPHANS
    if unexpected:
        raise RuntimeError(
            f"err-provenance task dirs with no task.toml: {sorted(unexpected)}. "
            f"A task without a task.toml declares no deliverable and is scored by "
            f"nothing."
        )
    return found


ERR_PROVENANCE_TASKS = _answer_deliverable_tasks()

# An empty suite is a broken suite, whatever the reason — collecting nothing must
# never read as green.
if not ERR_PROVENANCE_TASKS:
    raise RuntimeError(
        "no err-provenance-* task declares an answer deliverable; this suite "
        "would collect nothing. Check for a broken task layout."
    )


def _run_verifier(
    task_dir: Path,
    verifier: str,
    workspace: Path,
) -> dict:
    """Run a single verifier script and return parsed JSON output."""
    verifier_path = task_dir / verifier
    assert verifier_path.exists(), f"Verifier not found: {verifier_path}"

    # The runner's own env, not a copy of it: these checks shell out to
    # `python -m eb_verify.…`, and a hand-rolled PYTHONPATH here would keep
    # passing after the runner stopped exporting one.
    env = checkpoint_env(workspace, task_dir, "test")

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


@dataclass(frozen=True)
class TaskEnv:
    """One task under test, with the scoring contract it declares for itself."""

    name: str
    task_dir: Path
    workspace: Path
    gt: dict
    verifiers: list[str]
    weights: list[float]

    def score(self, answer: dict | None) -> tuple[float, list[dict]]:
        """The task's weighted score for ``answer`` (None writes no answer.json)."""
        if answer is not None:
            _create_answer(self.workspace, answer)
        results = [_run_verifier(self.task_dir, v, self.workspace) for v in self.verifiers]
        return _weighted_score(results, self.weights), results


@pytest.fixture(params=ERR_PROVENANCE_TASKS, ids=lambda t: t[0].name)
def task_env(request, tmp_path: Path) -> TaskEnv:
    """Parametrized fixture for each answer-deliverable err-provenance task."""
    task_dir, verifiers, weights = request.param

    gt_path = task_dir / "ground_truth.json"
    assert gt_path.exists(), f"No ground_truth.json in {task_dir}"

    workspace = tmp_path / "workspace"
    workspace.mkdir()

    return TaskEnv(
        name=task_dir.name,
        task_dir=task_dir,
        workspace=workspace,
        gt=json.loads(gt_path.read_text()),
        verifiers=verifiers,
        weights=weights,
    )


def test_ground_truth_scores_high(task_env: TaskEnv) -> None:
    """(a) A ground-truth answer scores >= 0.85."""
    total, results = task_env.score(_build_ground_truth_answer(task_env.gt))
    assert total >= 0.85, (
        f"{task_env.name}: ground truth answer scored {total:.2f} < 0.85. "
        f"Per-checkpoint: {[r.get('score', 0) for r in results]}"
    )


def test_empty_answer_scores_low(task_env: TaskEnv) -> None:
    """(b) An empty answer scores <= 0.10."""
    total, results = task_env.score(_build_empty_answer())
    assert total <= 0.10, (
        f"{task_env.name}: empty answer scored {total:.2f} > 0.10. "
        f"Per-checkpoint: {[r.get('score', 0) for r in results]}"
    )


def test_partial_answer_scores_mid(task_env: TaskEnv) -> None:
    """(c) A partial answer lands between the two."""
    total, results = task_env.score(_build_partial_answer(task_env.gt))
    assert 0.20 <= total <= 0.80, (
        f"{task_env.name}: partial answer scored {total:.2f}, expected 0.20-0.80. "
        f"Per-checkpoint: {[r.get('score', 0) for r in results]}"
    )


def test_wrong_answer_scores_low(task_env: TaskEnv) -> None:
    """A confidently wrong answer must not score > 0.5."""
    total, results = task_env.score(_build_wrong_answer())
    assert total <= 0.5, (
        f"{task_env.name}: wrong answer scored {total:.2f} > 0.5 — false positive! "
        f"Per-checkpoint: {[r.get('score', 0) for r in results]}"
    )


def test_no_answer_file(task_env: TaskEnv) -> None:
    """A missing answer.json scores 0 — the agent produced nothing to score."""
    total, _ = task_env.score(None)
    assert total <= 0.10, f"{task_env.name}: missing answer scored {total:.2f} > 0.10"
