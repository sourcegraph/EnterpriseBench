"""Regression tests for LLM-judge artifact discovery + no-output soundness.

Covers two verifier-soundness holes in run_task._apply_llm_judge
(EnterpriseBench-hmcp, audit-2026-06-22 findings #1 and #2):

  Finding #1 (silent_fallbacks): when no agent artifact is found in the
  container, the judge used to ``return scores`` — recording the UN-CAPPED
  grep score as the real Tier-2 measurement. It must instead tag the scores
  with ``verifier_infra_error`` so the run routes to the re-run channel.

  Finding #2 (hardcoded artifact paths): artifact discovery was a baked-in set
  of task-specific repo paths (``/workspace/moby/INCIDENT_REPORT.md`` …). Any
  task whose artifact lives elsewhere (e.g. ``/workspace/BLAST_RADIUS.md``)
  yielded empty output and silently routed into finding #1. Candidate paths
  must be derived from task metadata (instruction.md) instead.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_ROOT / "scripts" / "orchestration"))
sys.path.insert(0, str(_ROOT / "scripts" / "infra"))
# eb_verify.judge is imported lazily inside _apply_llm_judge; put lib/ on the
# path here so the positive-path test can patch eb_verify.judge.LLMJudge.
sys.path.insert(0, str(_ROOT / "lib"))

import run_task  # noqa: E402
from run_task import (  # noqa: E402
    ANSWER_ARTIFACT_PATH,
    _apply_llm_judge,
    _derive_artifact_candidates,
)


def _exec_result(returncode: int, stdout: str) -> MagicMock:
    m = MagicMock()
    m.returncode = returncode
    m.stdout = stdout
    return m


def _docker_exec_for(contents: dict[str, str]):
    """Build a _docker_exec side_effect that returns ``contents[path]`` for
    ``cat <path>`` calls (empty/exit-1 for any path not present).

    Accepts ``user`` because the judge now tries the root-owned answer-artifact
    snapshot (``cat <snapshot> ... user="root"``) before the live path — this
    helper stands in for both calls, and ``contents`` is keyed by whichever
    path (snapshot or live) the caller wants to simulate as present.
    """

    def side_effect(container_id, cmd, timeout=5, user=None):
        path = cmd[-1]
        if path in contents:
            return _exec_result(0, contents[path])
        return _exec_result(1, "")

    return side_effect


def _write_task(tmp_path: Path, instruction: str, checkpoints_gt: dict) -> Path:
    (tmp_path / "instruction.md").write_text(instruction)
    (tmp_path / "expected_solution.json").write_text(
        json.dumps({"task_id": "t1", "checkpoints": checkpoints_gt})
    )
    return tmp_path


# --------------------------------------------------------------------------
# Finding #2 — candidate paths derived from metadata, no baked-in repo names
# --------------------------------------------------------------------------


class TestDeriveArtifactCandidates:
    def test_answer_json_is_always_first_candidate(self, tmp_path: Path) -> None:
        (tmp_path / "instruction.md").write_text("Do the task.")
        candidates = _derive_artifact_candidates(tmp_path)
        assert candidates[0] == ANSWER_ARTIFACT_PATH

    def test_extracts_task_declared_output_path(self, tmp_path: Path) -> None:
        # The path the old hardcoded list would have MISSED.
        (tmp_path / "instruction.md").write_text(
            "## Output\nWrite your findings to `/workspace/BLAST_RADIUS.md`.\n"
        )
        candidates = _derive_artifact_candidates(tmp_path)
        assert "/workspace/BLAST_RADIUS.md" in candidates
        assert ANSWER_ARTIFACT_PATH in candidates

    def test_extracts_nested_and_non_md_paths(self, tmp_path: Path) -> None:
        (tmp_path / "instruction.md").write_text(
            "Write the report to `/workspace/analysis/IMPACT_REPORT.md` and the "
            "drift summary to /workspace/DRIFT_REPORT.json when done."
        )
        candidates = _derive_artifact_candidates(tmp_path)
        assert "/workspace/analysis/IMPACT_REPORT.md" in candidates
        assert "/workspace/DRIFT_REPORT.json" in candidates

    def test_missing_instruction_yields_only_answer_json(self, tmp_path: Path) -> None:
        candidates = _derive_artifact_candidates(tmp_path)
        assert candidates == [ANSWER_ARTIFACT_PATH]

    def test_candidates_independent_of_repo_names(self, tmp_path: Path) -> None:
        # Two tasks with the same output contract but different repos derive
        # the same candidate set — no repo-name coupling (the finding #2 smell).
        (tmp_path / "instruction.md").write_text(
            "Repo is at /workspace/some-unrelated-repo. "
            "Write to `/workspace/REFACTOR_PLAN.md`."
        )
        candidates = _derive_artifact_candidates(tmp_path)
        assert "/workspace/REFACTOR_PLAN.md" in candidates
        # The repo dir (no recognised artifact extension) is not a candidate.
        assert not any(c.endswith("some-unrelated-repo") for c in candidates)


# --------------------------------------------------------------------------
# Finding #1 — no agent output must NOT pass through un-capped grep
# --------------------------------------------------------------------------


class TestNoAgentOutputRoutesToInfraError:
    def _scores(self) -> dict:
        return {
            "task_score": 4.0,
            "checkpoints": [
                {"name": "cp1", "score": 1.0, "weight": 1.0, "passed": True}
            ],
        }

    def test_no_output_tags_verifier_infra_error(self, tmp_path: Path) -> None:
        task_dir = _write_task(
            tmp_path,
            "Write findings to `/workspace/agent_output/answer.json`.",
            {"cp1": {"expected_solution": "x", "evaluation_criteria": []}},
        )
        scores = self._scores()
        with patch.object(
            run_task, "_docker_exec", side_effect=_docker_exec_for({})
        ):
            out = _apply_llm_judge(scores, task_dir, "cid", {"task": {}})

        assert "verifier_infra_error" in out
        assert out["verifier_infra_error"]["reason"] == "no_agent_output"
        # The un-capped grep score must NOT have been promoted to a judged final:
        # no judge_score was attached to the checkpoint (cap was never applied).
        assert "judge_score" not in out["checkpoints"][0]

    def test_path_miss_still_tried_then_infra_error(self, tmp_path: Path) -> None:
        # Artifact declared at a path the OLD hardcoded list never tried.
        task_dir = _write_task(
            tmp_path,
            "## Output\nWrite to `/workspace/BLAST_RADIUS.md`.\n",
            {"cp1": {"expected_solution": "x", "evaluation_criteria": []}},
        )
        mock = MagicMock(side_effect=_docker_exec_for({}))
        with patch.object(run_task, "_docker_exec", mock):
            out = _apply_llm_judge(self._scores(), task_dir, "cid", {"task": {}})

        # The derived path was actually attempted (proves finding #2 fix).
        tried = {call.args[1][-1] for call in mock.call_args_list}
        assert "/workspace/BLAST_RADIUS.md" in tried
        assert ANSWER_ARTIFACT_PATH in tried
        # And with nothing found anywhere, it routes to infra error.
        assert "verifier_infra_error" in out


class TestArtifactFoundAtDerivedPathAppliesCap:
    """Positive path: when the artifact IS found at a metadata-derived,
    non-answer.json location, the Tier-2 cap is applied (min(grep, judge))."""

    def test_cap_applied_at_blast_radius_path(self, tmp_path: Path) -> None:
        task_dir = _write_task(
            tmp_path,
            "## Output\nWrite to `/workspace/BLAST_RADIUS.md`.\n",
            {"cp1": {"expected_solution": "expected", "evaluation_criteria": ["c"]}},
        )
        scores = {
            "task_score": 1.0,
            "checkpoints": [
                {"name": "cp1", "score": 1.0, "weight": 1.0, "passed": True}
            ],
        }
        # answer.json empty, artifact present at the derived path.
        side = _docker_exec_for({"/workspace/BLAST_RADIUS.md": "agent report body"})

        fake_judge = MagicMock()
        fake_judge.evaluate_checkpoint.return_value = MagicMock(
            score=0.5, reasoning="partial"
        )

        with patch.object(run_task, "_docker_exec", side_effect=side), patch(
            "eb_verify.judge.LLMJudge", return_value=fake_judge
        ):
            out = _apply_llm_judge(scores, task_dir, "cid", {"task": {}})

        assert "verifier_infra_error" not in out
        cp = out["checkpoints"][0]
        assert cp["judge_score"] == 0.5
        assert cp["grep_score"] == 1.0
        assert cp["score"] == 0.5  # min(grep, judge)


if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-v"]))
