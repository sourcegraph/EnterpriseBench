"""Planted-prompt and prompt-echo evidence vectors → free score (over-credit).

The harness plants `/workspace/instruction.md` and chowns it to the agent, so it
is agent-readable by design. A check that gathers its evidence by globbing
`$WORKSPACE` therefore reads the *question* and grades it as the *answer*: a
no-op agent that writes nothing scores full marks.

Scoping the scan to the agent-owned `agent_output/` is necessary but NOT
sufficient. If the check greps for tokens the prompt itself supplies, an agent
that echoes the prompt back into `answer.json` still passes. The evidence must
be matched against the answer key — `ground_truth.json` is sealed root-only
(run_task.py: GRADING_PATHS), so its paths are reachable only by an agent that
actually read the repo.

Origin: hpcsv (reproduced by the no5dn review worker on main).
"""

from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path

import pytest


REPO_ROOT = Path(__file__).parent.parent.parent
TASK_DIR = (
    REPO_ROOT
    / "benchmarks"
    / "incident_response"
    / "ansible-galaxy-tar-regression-prove-001"
)
CHECK = TASK_DIR / "checks" / "check_root_cause.sh"

# The two answer-key paths. Neither appears in instruction.md or
# instruction_mcp.md, and ground_truth.json is sealed away from the agent — so
# naming them is only possible after reading the repo.
GT_PATHS = [
    "/workspace/ansible/lib/ansible/galaxy/collection/__init__.py",
    "/workspace/ansible/lib/ansible/galaxy/collection/concrete_artifact_manager.py",
]


def _run_check(workspace: Path) -> dict:
    """Run check_root_cause.sh against a workspace and return its verdict."""
    env = os.environ.copy()
    env["WORKSPACE"] = str(workspace)
    env["TASK_DIR"] = str(TASK_DIR)
    env["TASK_ID"] = "test"

    result = subprocess.run(
        ["bash", str(CHECK)],
        capture_output=True,
        text=True,
        timeout=30,
        cwd=str(workspace),
        env=env,
    )
    stdout = result.stdout.strip()
    assert stdout, f"check emitted no verdict (stderr: {result.stderr})"
    return json.loads(stdout)


def _plant_prompt(workspace: Path) -> None:
    """Reproduce exactly what run_task.py plants: the agent-owned prompt."""
    (workspace / "instruction.md").write_text(
        (TASK_DIR / "instruction.md").read_text()
    )


def _write_answer(workspace: Path, answer: dict) -> None:
    out = workspace / "agent_output"
    out.mkdir(parents=True, exist_ok=True)
    (out / "answer.json").write_text(json.dumps(answer))


def test_no_op_agent_with_planted_prompt_scores_zero(tmp_path: Path) -> None:
    """The core vector: workspace holds only the planted prompt, agent did nothing."""
    _plant_prompt(tmp_path)

    verdict = _run_check(tmp_path)

    assert verdict["score"] == 0.0, (
        f"no-op agent scored {verdict['score']} off the planted prompt: {verdict}"
    )
    assert verdict["passed"] is False


def test_prompt_echoed_answer_json_scores_zero(tmp_path: Path) -> None:
    """D1: echoing the prompt back into answer.json is not evidence of anything."""
    _plant_prompt(tmp_path)
    _write_answer(
        tmp_path,
        {
            "summary": (TASK_DIR / "instruction.md").read_text(),
            "error_chain": ["tarfile getmember on the galaxy collection tar members"],
        },
    )

    verdict = _run_check(tmp_path)

    assert verdict["score"] == 0.0, (
        f"prompt-echoed answer.json scored {verdict['score']}: {verdict}"
    )
    assert verdict["passed"] is False


def test_planted_prompt_does_not_top_up_a_real_answer(tmp_path: Path) -> None:
    """A half-right answer must stay half-right with the prompt sitting next to it."""
    _plant_prompt(tmp_path)
    _write_answer(tmp_path, {"source_files": [{"path": GT_PATHS[0]}]})

    verdict = _run_check(tmp_path)

    assert verdict["score"] == 0.5, f"expected 0.5 for 1/2 files, got {verdict}"


def test_answer_json_symlink_is_not_followed(tmp_path: Path) -> None:
    """agent_output/ is agent-owned; a symlinked answer.json is not a real answer."""
    _plant_prompt(tmp_path)
    real = tmp_path / "elsewhere.json"
    real.write_text(json.dumps({"source_files": [{"path": p} for p in GT_PATHS]}))
    out = tmp_path / "agent_output"
    out.mkdir()
    (out / "answer.json").symlink_to(real)

    verdict = _run_check(tmp_path)

    assert verdict["score"] == 0.0, f"followed a symlinked answer.json: {verdict}"
    assert verdict["passed"] is False


def test_oversized_answer_json_scores_zero(tmp_path: Path) -> None:
    """A multi-megabyte answer.json is a grader-DoS, not a findings file."""
    _plant_prompt(tmp_path)
    _write_answer(
        tmp_path,
        {
            "source_files": [{"path": p} for p in GT_PATHS],
            "padding": "x" * (2 * 1024 * 1024),
        },
    )

    verdict = _run_check(tmp_path)

    assert verdict["score"] == 0.0, f"accepted an oversized answer.json: {verdict}"
    assert verdict["passed"] is False


def test_malformed_answer_json_scores_zero(tmp_path: Path) -> None:
    """Unparseable agent output scores 0 — and must not crash the check."""
    _plant_prompt(tmp_path)
    out = tmp_path / "agent_output"
    out.mkdir()
    (out / "answer.json").write_text("{not json")

    verdict = _run_check(tmp_path)

    assert verdict["score"] == 0.0
    assert verdict["passed"] is False


# --- Negative controls: the guard must not under-credit a real answer ---------


def test_ground_truth_answer_scores_full(tmp_path: Path) -> None:
    """A genuine answer naming both answer-key files still scores 1.0."""
    _plant_prompt(tmp_path)
    _write_answer(tmp_path, {"source_files": [{"path": p} for p in GT_PATHS]})

    verdict = _run_check(tmp_path)

    assert verdict["score"] == 1.0, f"real answer under-credited: {verdict}"
    assert verdict["passed"] is True


def test_code_paths_field_is_accepted(tmp_path: Path) -> None:
    """The output appendix offers `code_paths` too — honour it like `source_files`."""
    _plant_prompt(tmp_path)
    _write_answer(tmp_path, {"code_paths": [{"path": p} for p in GT_PATHS]})

    verdict = _run_check(tmp_path)

    assert verdict["score"] == 1.0, f"code_paths answer under-credited: {verdict}"
    assert verdict["passed"] is True


def test_plain_string_paths_are_accepted(tmp_path: Path) -> None:
    """Agents commonly emit bare strings rather than {"path": ...} objects."""
    _plant_prompt(tmp_path)
    _write_answer(tmp_path, {"source_files": GT_PATHS})

    verdict = _run_check(tmp_path)

    assert verdict["score"] == 1.0, f"string-path answer under-credited: {verdict}"
    assert verdict["passed"] is True

