"""Runner-level tests for ground_truth.require_grounded_citations pass-through.

End-to-end: a task whose ground truth sets require_grounded_citations=true
must have that flag forwarded to artifact validators that support it, and
must surface an explicit validation issue for validators that do not.
"""

from __future__ import annotations

import json
from pathlib import Path

from eb_verify.runner import CheckpointRunner
from eb_verify.task_parser import ArtifactSpec, Checkpoint, GroundTruth, TaskDefinition


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

# A span long enough to clear MIN_SPAN_CHARS (20) after normalization.
SOURCE_LINE = "connection_pool.acquire(timeout=30) raised PoolExhausted"


def make_task(
    artifacts_required: list[str],
    ground_truth: GroundTruth | None,
    checkpoints: list[Checkpoint] | None = None,
) -> TaskDefinition:
    return TaskDefinition(
        id="grounded-test-001",
        suite="incident_response",
        difficulty="medium",
        session_type="single",
        repos=[],
        checkpoints=checkpoints or [],
        artifacts=ArtifactSpec(required=artifacts_required),
        ground_truth=ground_truth,
    )


def make_passing_checkpoint(task_dir: Path) -> Checkpoint:
    """A checkpoint whose verifier always scores 1.0."""
    task_dir.mkdir(parents=True, exist_ok=True)
    verifier = task_dir / "check_pass.sh"
    verifier.write_text('#!/bin/bash\necho \'{"score": 1.0, "detail": "ok"}\'\n')
    return Checkpoint(name="cp1", weight=1.0, verifier="check_pass.sh")


def write_workspace(tmp_path: Path, evidence_span: str) -> Path:
    """Build a workspace with one source file and an incident report citing it."""
    workspace = tmp_path / "ws"
    src = workspace / "repo-a" / "src" / "pool.py"
    src.parent.mkdir(parents=True)
    src.write_text(f"# db pool\n{SOURCE_LINE}\n")

    report = {
        "timeline": [{"time": "2026-07-01T10:00:00Z", "event": "pool exhausted"}],
        "root_cause": "connection pool exhaustion",
        "remediation": "raise pool size",
        "affected_services": ["checkout"],
        "citations": [
            {"repo": "repo-a", "file": "src/pool.py", "evidence_span": evidence_span}
        ],
    }
    (workspace / "incident_report.json").write_text(json.dumps(report))
    return workspace


# ---------------------------------------------------------------------------
# Flag set — validator supports the kwarg
# ---------------------------------------------------------------------------

class TestGroundedCitationsEnforced:
    def test_fabricated_citation_fails_with_reason(self, tmp_path):
        workspace = write_workspace(
            tmp_path,
            evidence_span="this span was fabricated and appears in no file",
        )
        task = make_task(
            ["incident_report"], GroundTruth(require_grounded_citations=True)
        )
        runner = CheckpointRunner(task=task, workspace=workspace)

        results = runner.validate_artifacts()

        assert len(results) == 1
        assert results[0]["valid"] is False
        assert "span_not_found" in results[0]["detail"]

    def test_grounded_citation_passes(self, tmp_path):
        workspace = write_workspace(tmp_path, evidence_span=SOURCE_LINE)
        task = make_task(
            ["incident_report"], GroundTruth(require_grounded_citations=True)
        )
        runner = CheckpointRunner(task=task, workspace=workspace)

        results = runner.validate_artifacts()

        assert len(results) == 1
        assert results[0]["valid"] is True, results[0]["detail"]


class TestAnswerArtifactEnforced:
    """The err-provenance-tri-httpx-* tasks use artifacts.required=["answer"]."""

    def _write_answer_workspace(self, tmp_path: Path, evidence_span: str) -> Path:
        workspace = tmp_path / "ws"
        src = workspace / "repo-a" / "src" / "pool.py"
        src.parent.mkdir(parents=True)
        src.write_text(f"# db pool\n{SOURCE_LINE}\n")
        answer = {
            "source_files": [{"path": "repo-a/src/pool.py"}],
            "citations": [
                {"repo": "repo-a", "file": "src/pool.py", "evidence_span": evidence_span}
            ],
        }
        (workspace / "answer.json").write_text(json.dumps(answer))
        return workspace

    def test_grounded_answer_passes(self, tmp_path):
        workspace = self._write_answer_workspace(tmp_path, evidence_span=SOURCE_LINE)
        task = make_task(["answer"], GroundTruth(require_grounded_citations=True))
        runner = CheckpointRunner(task=task, workspace=workspace)

        results = runner.validate_artifacts()

        assert len(results) == 1
        assert results[0]["valid"] is True, results[0]["detail"]

    def test_fabricated_answer_citation_fails_with_reason(self, tmp_path):
        workspace = self._write_answer_workspace(
            tmp_path, evidence_span="this span was fabricated and appears in no file"
        )
        task = make_task(["answer"], GroundTruth(require_grounded_citations=True))
        runner = CheckpointRunner(task=task, workspace=workspace)

        results = runner.validate_artifacts()

        assert results[0]["valid"] is False
        assert "span_not_found" in results[0]["detail"]


# ---------------------------------------------------------------------------
# Flag set — validator does NOT support the kwarg
# ---------------------------------------------------------------------------

class TestUnsupportedValidator:
    def test_flag_with_unsupported_validator_is_explicit_issue(self, tmp_path):
        # runbook's validate() takes only (workspace) — no groundedness support.
        workspace = tmp_path / "ws"
        workspace.mkdir()
        task = make_task(["runbook"], GroundTruth(require_grounded_citations=True))
        runner = CheckpointRunner(task=task, workspace=workspace)

        results = runner.validate_artifacts()

        assert len(results) == 1
        assert results[0]["valid"] is False
        assert "require_grounded_citations" in results[0]["detail"]
        assert "runbook" in results[0]["detail"]
        # A task/harness config mismatch the agent cannot influence: the gate
        # the task demands was never applied, so this is the infra channel,
        # not an agent failure the grounding gate may zero.
        assert results[0]["infra_error"]["cause"] == "groundedness_unsupported"


# ---------------------------------------------------------------------------
# Flag unset — behavior unchanged
# ---------------------------------------------------------------------------

class TestFlagUnset:
    def test_fabricated_citation_passes_without_flag(self, tmp_path):
        workspace = write_workspace(
            tmp_path,
            evidence_span="this span was fabricated and appears in no file",
        )
        task = make_task(["incident_report"], ground_truth=None)
        runner = CheckpointRunner(task=task, workspace=workspace)

        results = runner.validate_artifacts()

        assert results[0]["valid"] is True, results[0]["detail"]

    def test_flag_false_not_enforced(self, tmp_path):
        workspace = write_workspace(
            tmp_path,
            evidence_span="this span was fabricated and appears in no file",
        )
        task = make_task(
            ["incident_report"], GroundTruth(require_grounded_citations=False)
        )
        runner = CheckpointRunner(task=task, workspace=workspace)

        results = runner.validate_artifacts()

        assert results[0]["valid"] is True, results[0]["detail"]


# ---------------------------------------------------------------------------
# Score gating — the flag must have end-to-end teeth, not just a side channel
# ---------------------------------------------------------------------------

class TestScoreGating:
    def _run(self, tmp_path: Path, evidence_span: str, ground_truth):
        workspace = write_workspace(tmp_path, evidence_span=evidence_span)
        task_dir = tmp_path / "task"
        checkpoint = make_passing_checkpoint(task_dir)
        task = make_task(["incident_report"], ground_truth, checkpoints=[checkpoint])
        runner = CheckpointRunner(task=task, task_dir=task_dir, workspace=workspace)
        return runner.run_all(output_path=tmp_path / "reward.txt")

    def test_ungrounded_artifact_zeroes_total_score(self, tmp_path):
        result = self._run(
            tmp_path,
            evidence_span="this span was fabricated and appears in no file",
            ground_truth=GroundTruth(require_grounded_citations=True),
        )
        # The checkpoint scored 1.0, but the required artifact failed the
        # groundedness gate the task demands — the total must be zeroed,
        # not just annotated in a side channel nothing reads.
        assert result.checkpoint_results[0].score == 1.0
        assert result.artifact_results[0]["valid"] is False
        assert result.total_score == 0.0
        assert result.score_gates, "gating must be recorded for the summary"
        assert "require_grounded_citations" in result.summary()

    def test_grounded_artifact_keeps_score(self, tmp_path):
        result = self._run(
            tmp_path,
            evidence_span=SOURCE_LINE,
            ground_truth=GroundTruth(require_grounded_citations=True),
        )
        assert result.artifact_results[0]["valid"] is True
        assert result.total_score == 1.0
        assert result.score_gates == []

    def test_no_flag_invalid_artifact_does_not_zero(self, tmp_path):
        # Scoped gate: without the flag, legacy behavior is unchanged even
        # if an artifact validator reports issues.
        result = self._run(
            tmp_path,
            evidence_span="this span was fabricated and appears in no file",
            ground_truth=None,
        )
        assert result.total_score == 1.0
        assert result.score_gates == []
