"""An artifact the harness could not CHECK must never score the agent 0.0.

Same family as the checkpoint no-verdict rule in test_checkpoint_verdict.py, one
level up on the ARTIFACT channel.

Bead EnterpriseBench-mzve9. The bug these lock down: ``valid=False`` was
overloaded, meaning both "the agent's artifact is genuinely bad" and "the
harness could not examine it at all" (validator unregistered on a numpy-less
sandbox, scoring deps failed to load, or the task demands a groundedness gate
the validator cannot enforce). Under ``require_grounded_citations`` the score
gate read the second case as the first and forced total_score to 0.0.

The invariant pinned here: an artifact that was never checked carries
``infra_error`` (routing key :data:`~eb_verify.scorer_guard.NO_VERDICT_REASON`)
and, when the grounding gate would have read its validity, invalidates the run
into the re-run channel. Ungated, artifact validity carries no score weight, so
the checkpoint total stands and the artifact is only marked not-checked — no
wasted re-run.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(REPO_ROOT / "lib"))

from eb_verify.cli import main  # noqa: E402
from eb_verify.plugins import get_validator  # noqa: E402
from eb_verify.plugins.fact_triples import FactTriplesValidator  # noqa: E402
from eb_verify.fact_coverage import ScoringDepsUnavailable  # noqa: E402
from eb_verify.runner import CheckpointRunner  # noqa: E402
from eb_verify.scorer_guard import NO_VERDICT_REASON  # noqa: E402
from eb_verify.task_parser import (  # noqa: E402
    ArtifactSpec,
    Checkpoint,
    GroundTruth,
    TaskDefinition,
)

# A span long enough to clear groundedness.MIN_SPAN_CHARS (20).
SOURCE_LINE = "connection_pool.acquire(timeout=30) raised PoolExhausted"

# Also >MIN_SPAN_CHARS, but present in no workspace file: groundedness fails
# on the citation itself — a genuine agent failure, not an infra one.
FABRICATED_SPAN = "this span was fabricated and appears in no file"

# GroundTruth is frozen, so one gated instance serves every test.
GROUNDED = GroundTruth(require_grounded_citations=True)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def make_task(
    artifacts_required: list[str],
    ground_truth: GroundTruth | None,
    checkpoints: list[Checkpoint] | None = None,
) -> TaskDefinition:
    return TaskDefinition(
        id="artifact-infra-001",
        suite="incident_response",
        difficulty="medium",
        session_type="single",
        repos=[],
        checkpoints=checkpoints or [],
        artifacts=ArtifactSpec(required=artifacts_required),
        ground_truth=ground_truth,
    )


def make_passing_checkpoint(task_dir: Path) -> Checkpoint:
    task_dir.mkdir(parents=True, exist_ok=True)
    (task_dir / "check_pass.sh").write_text(
        '#!/bin/bash\necho \'{"score": 1.0, "detail": "ok"}\'\n'
    )
    return Checkpoint(name="cp1", weight=1.0, verifier="check_pass.sh")


def write_incident_workspace(tmp_path: Path, evidence_span: str) -> Path:
    """A workspace with one source file and an incident report citing it."""
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


def write_fact_triples_workspace(tmp_path: Path) -> Path:
    """Schema-valid facts + ground truth, so validate() reaches the deps load."""
    workspace = tmp_path / "ws"
    (workspace / "repo").mkdir(parents=True)
    (workspace / "ground_truth").mkdir()
    span = "score = 1.0 if passed else 0.0"
    (workspace / "repo" / "milestone.py").write_text(f"def run():\n    {span}\n")
    fact = {
        "subject": "milestone.py",
        "predicate": "fabricates",
        "object": "scores",
        "statement": "milestone.py fabricates a score from the process exit code",
        "evidence": {"repo": "repo", "file": "milestone.py", "span": span},
        "confidence": 90,
    }
    (workspace / "facts.json").write_text(json.dumps({"facts": [fact]}))
    (workspace / "ground_truth" / "expected_facts.json").write_text(
        json.dumps({"facts": [{k: fact[k] for k in ("subject", "predicate", "object", "statement")}]})
    )
    return workspace


def _deps_blown(monkeypatch: pytest.MonkeyPatch) -> None:
    """Make the scoring-deps load fail the way a numpy-less sandbox does."""

    def _raise() -> None:
        raise ScoringDepsUnavailable("No module named 'numpy' (simulated sandbox)")

    monkeypatch.setattr(
        "eb_verify.plugins.fact_triples.load_scoring_deps", _raise
    )


def run_full_verification(
    tmp_path: Path,
    artifacts_required: list[str],
    *,
    ground_truth: GroundTruth | None = GROUNDED,
    evidence_span: str = SOURCE_LINE,
    checkpoint: Checkpoint | None = None,
):
    """run_all over an incident workspace. Defaults to the all-healthy gated
    run: one passing checkpoint, one genuinely grounded citation."""
    workspace = write_incident_workspace(tmp_path, evidence_span=evidence_span)
    task_dir = tmp_path / "task"
    task_dir.mkdir()
    checkpoint = checkpoint or make_passing_checkpoint(task_dir)
    task = make_task(artifacts_required, ground_truth, checkpoints=[checkpoint])
    runner = CheckpointRunner(task=task, task_dir=task_dir, workspace=workspace)
    return runner.run_all(output_path=tmp_path / "reward.txt")


# ---------------------------------------------------------------------------
# The three not-checked causes each route to the infra channel
# ---------------------------------------------------------------------------

class TestNotCheckedRoutesToInfraChannel:
    def test_unregistered_validator_is_infra(self, tmp_path: Path) -> None:
        task = make_task(["unknown_type_xyz"], ground_truth=None)
        runner = CheckpointRunner(task=task, workspace=tmp_path)

        results = runner.validate_artifacts()

        assert len(results) == 1
        error = results[0]["infra_error"]
        assert error is not None, "an unchecked artifact must carry infra_error"
        assert error["reason"] == NO_VERDICT_REASON
        assert error["cause"] == "validator_unregistered"
        assert error["stage"] == "artifact_validation"
        assert error["artifact_type"] == "unknown_type_xyz"

    def test_groundedness_unsupported_is_infra(self, tmp_path: Path) -> None:
        # runbook's validate() takes only (workspace): the task demands a gate
        # the validator cannot enforce — a static config mismatch, not a move
        # the agent could have played differently.
        task = make_task(["runbook"], GROUNDED)
        runner = CheckpointRunner(task=task, workspace=tmp_path)

        results = runner.validate_artifacts()

        error = results[0]["infra_error"]
        assert error is not None
        assert error["reason"] == NO_VERDICT_REASON
        assert error["cause"] == "groundedness_unsupported"

    def test_scoring_deps_unavailable_is_infra(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        _deps_blown(monkeypatch)
        workspace = write_fact_triples_workspace(tmp_path)

        result = FactTriplesValidator().validate(workspace)

        assert result.infra_error is not None
        assert result.infra_error.reason == NO_VERDICT_REASON
        assert result.infra_error.cause == "scoring_deps_unavailable"
        assert "numpy" in result.detail

    def test_agent_failure_carries_no_infra_error(self, tmp_path: Path) -> None:
        """The channel must not over-fire: a genuinely bad artifact stays an
        agent failure the gate is allowed to read."""
        workspace = write_incident_workspace(tmp_path, evidence_span=FABRICATED_SPAN)
        task = make_task(["incident_report"], GROUNDED)
        runner = CheckpointRunner(task=task, workspace=workspace)

        results = runner.validate_artifacts()

        assert results[0]["valid"] is False
        assert results[0]["infra_error"] is None

    def test_artifact_results_stay_json_serializable(self, tmp_path: Path) -> None:
        """Smoke/integration consumers json.dumps the artifact list verbatim."""
        task = make_task(["unknown_type_xyz"], ground_truth=None)
        runner = CheckpointRunner(task=task, workspace=tmp_path)

        results = runner.validate_artifacts()

        round_tripped = json.loads(json.dumps(results))
        assert round_tripped[0]["infra_error"]["cause"] == "validator_unregistered"


# ---------------------------------------------------------------------------
# Gated runs: 'not checked' invalidates the run, never gates it to 0.0
# ---------------------------------------------------------------------------

class TestGatedRunInvalidatedNotZeroed:
    def test_unchecked_artifact_invalidates_run(self, tmp_path: Path) -> None:
        result = run_full_verification(tmp_path, ["unknown_type_xyz"])

        # The run routes to the re-run channel: nobody measured the artifact
        # the gate needs, so there is no score — least of all a 0.0.
        assert result.verifier_infra_error is not None
        assert result.verifier_infra_error["cause"] == "validator_unregistered"
        assert result.score_gates == [], (
            "the grounding gate must not read an unchecked artifact's "
            "placeholder valid=False as an agent failure"
        )

    def test_invalid_summary_never_prints_a_number(self, tmp_path: Path) -> None:
        result = run_full_verification(tmp_path, ["unknown_type_xyz"])
        summary = result.summary()

        assert "total_score: INVALID" in summary
        assert "- unknown_type_xyz: INFRA_ERROR (not checked)" in summary
        assert "score_gate" not in summary

    def test_genuinely_bad_artifact_still_gates_to_zero(self, tmp_path: Path) -> None:
        """The fix must not defang the gate: a checked-and-failed artifact
        under the flag still zeroes the total (agent failure, exit 1)."""
        result = run_full_verification(
            tmp_path, ["incident_report"], evidence_span=FABRICATED_SPAN
        )

        assert result.verifier_infra_error is None
        assert result.total_score == 0.0
        assert result.score_gates, "the gate must still fire on a real failure"

    def test_checkpoint_infra_named_first(self, tmp_path: Path) -> None:
        """Both channels down: the routed error names the checkpoint failure,
        which occurs earlier in the pipeline and already invalidates the run."""
        missing = Checkpoint(name="cp1", weight=1.0, verifier="does_not_exist.sh")
        result = run_full_verification(
            tmp_path, ["unknown_type_xyz"], checkpoint=missing
        )

        assert result.verifier_infra_error is not None
        assert result.verifier_infra_error["cause"] == "missing_verifier"


# ---------------------------------------------------------------------------
# Non-gated runs: artifact validity is not score-bearing, so the score stands
# ---------------------------------------------------------------------------

class TestNonGatedRunKeepsItsScore:
    def test_unchecked_artifact_does_not_invalidate(self, tmp_path: Path) -> None:
        result = run_full_verification(
            tmp_path, ["unknown_type_xyz"], ground_truth=None
        )

        # The complete checkpoint measurement stands; forcing a re-run over an
        # artifact nobody's score depends on would waste the whole run.
        assert result.verifier_infra_error is None
        assert result.total_score == 1.0
        assert result.artifact_results[0]["infra_error"] is not None
        assert "INFRA_ERROR (not checked)" in result.summary()


# ---------------------------------------------------------------------------
# CLI: 'not checked' is exit 2 (re-run), never exit 1 (agent failed)
# ---------------------------------------------------------------------------

class TestCliExitCode:
    def test_validate_artifact_infra_exits_2(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys
    ) -> None:
        if get_validator("fact_triples") is None:
            pytest.skip("fact_triples not registered (scoring deps absent)")
        _deps_blown(monkeypatch)
        workspace = write_fact_triples_workspace(tmp_path)

        rc = main(["validate-artifact", "fact_triples", str(workspace)])

        assert rc == 2, "'could not check' must not collapse into exit 1"
        captured = capsys.readouterr()
        assert "INFRA_ERROR (not checked)" in captured.out
        assert "numpy" in captured.err
