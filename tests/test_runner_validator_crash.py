"""A crashing artifact validator must never kill the verification run.

Bead EnterpriseBench-s1o2n: runner.py and cli.py called validator.validate()
unguarded, so ANY exception a plugin validator raised aborted the entire run,
destroying every other artifact and checkpoint result with it.

The guard must also not encode the crash as a verdict: "the validator crashed"
is a HARNESS fault, and reading its ``valid=False`` as an agent failure would
fabricate a 0.0 under the require_grounded_citations gate. A crash routes to
the artifact infra channel instead: the artifact result carries ``infra_error``
with cause ``artifact_validator_crash`` (its ``valid=False`` is then a
placeholder), a gated run is invalidated into the re-run channel via
``VerificationResult.verifier_infra_error``, and a non-gated run keeps its
checkpoint total with the artifact reported as ``INFRA_ERROR (not checked)``.

This file covers the crash cause on all three paths, the two CLI call sites,
and the two other causes that leave an artifact unchecked — an unregistered
validator and a validator that cannot enforce a gate the task demands — which
were plain agent-blaming verdicts before this bead.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from eb_verify import plugins
from eb_verify.cli import main as cli_main
from eb_verify.runner import CheckpointRunner
from eb_verify.scorer_guard import NO_VERDICT_REASON
from eb_verify.task_parser import ArtifactSpec, Checkpoint, GroundTruth, TaskDefinition


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

CRASH_TYPE = "crash_test_artifact"
GROUNDED_CRASH_TYPE = "grounded_crash_test_artifact"
PROBE_CRASH_TYPE = "probe_crash_test_artifact"
UNREGISTERED_TYPE = "totally_unregistered_artifact"

# GroundTruth is frozen, so one gated instance serves every test.
GROUNDED = GroundTruth(require_grounded_citations=True)


class CrashingValidator:
    artifact_type = CRASH_TYPE

    def validate(self, workspace: Path):
        raise KeyError("boom: malformed artifact structure")


class GroundedCrashingValidator:
    artifact_type = GROUNDED_CRASH_TYPE

    def validate(self, workspace: Path, require_grounded_citations: bool = False):
        raise OSError("boom: unreadable artifact")


class ProbeCrashingValidator:
    """Crashes on .validate attribute access — the guarded block's earliest
    point of failure (before the inspect.signature probe can even run)."""

    artifact_type = PROBE_CRASH_TYPE

    @property
    def validate(self):
        raise RuntimeError("boom: validate not introspectable")


@pytest.fixture
def crashing_validators(monkeypatch):
    monkeypatch.setitem(plugins._registry, CRASH_TYPE, CrashingValidator())
    monkeypatch.setitem(
        plugins._registry, GROUNDED_CRASH_TYPE, GroundedCrashingValidator()
    )
    monkeypatch.setitem(
        plugins._registry, PROBE_CRASH_TYPE, ProbeCrashingValidator()
    )


def make_task(
    artifacts_required: list[str],
    ground_truth: GroundTruth | None = None,
    checkpoints: list[Checkpoint] | None = None,
) -> TaskDefinition:
    return TaskDefinition(
        id="crash-test-001",
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


def write_answer_workspace(tmp_path: Path) -> Path:
    """A workspace whose answer.json is genuinely valid (non-gated)."""
    workspace = tmp_path / "ws"
    workspace.mkdir(parents=True, exist_ok=True)
    answer = {"source_files": [{"path": "repo-a/src/pool.py"}], "citations": []}
    (workspace / "answer.json").write_text(json.dumps(answer))
    return workspace


def assert_infra_result(result: dict, cause: str) -> None:
    """The shape every not-checked artifact result must carry."""
    # valid=False is a placeholder — the verdict channel is infra_error, and
    # consumers must branch on it before reading valid.
    assert result["valid"] is False
    error = result["infra_error"]
    assert error is not None, "an unchecked artifact must carry infra_error"
    assert error["reason"] == NO_VERDICT_REASON
    assert error["cause"] == cause
    assert error["stage"] == "artifact_validation"
    assert error["artifact_type"] == result["type"]


# Every cause that leaves an artifact unchecked, and where it fails:
# (artifact_type, ground_truth, cause, detail_substr). The three crash causes
# cover the guarded block's distinct failure points — plain validate(), the
# grounded validate(), and the signature probe that precedes it. The two
# non-crash causes reach the same channel without any validator raising; both
# were plain agent-blaming verdicts before this bead.
UNCHECKED_CAUSES = [
    pytest.param(CRASH_TYPE, None, "artifact_validator_crash", "KeyError", id="crash"),
    pytest.param(
        GROUNDED_CRASH_TYPE, GROUNDED, "artifact_validator_crash", "OSError",
        id="crash-grounded-path",
    ),
    pytest.param(
        PROBE_CRASH_TYPE, GROUNDED, "artifact_validator_crash", "RuntimeError",
        id="crash-signature-probe",
    ),
    pytest.param(UNREGISTERED_TYPE, None, "validator_unregistered", None,
                 id="unregistered"),
    # runbook's validate() takes only (workspace) — no groundedness support.
    pytest.param("runbook", GROUNDED, "groundedness_unsupported", None,
                 id="groundedness-unsupported"),
]


# ---------------------------------------------------------------------------
# validate_artifacts: the fault is recorded, not raised
# ---------------------------------------------------------------------------

class TestValidateArtifactsGuard:
    @pytest.mark.parametrize(
        "artifact_type, ground_truth, cause, detail_substr", UNCHECKED_CAUSES
    )
    def test_unchecked_artifact_is_infra_not_a_verdict(
        self, tmp_path, crashing_validators, artifact_type, ground_truth,
        cause, detail_substr,
    ):
        """Every cause routes to the infra channel. Reaching the assertions at
        all is half the point: an unguarded validate() would raise out here."""
        task = make_task([artifact_type], ground_truth)
        runner = CheckpointRunner(task=task, workspace=tmp_path)

        (result,) = runner.validate_artifacts()

        assert_infra_result(result, cause)
        if detail_substr is not None:
            assert detail_substr in result["detail"]

    def test_other_artifacts_survive_a_crash(self, tmp_path, crashing_validators):
        """The core invariant: one crashing validator must not destroy the
        results of every OTHER artifact."""
        workspace = write_answer_workspace(tmp_path)
        task = make_task([CRASH_TYPE, "answer"])
        runner = CheckpointRunner(task=task, workspace=workspace)

        results = runner.validate_artifacts()

        by_type = {r["type"]: r for r in results}
        assert_infra_result(by_type[CRASH_TYPE], "artifact_validator_crash")
        assert by_type["answer"]["valid"] is True, by_type["answer"]["detail"]
        assert by_type["answer"]["infra_error"] is None


# ---------------------------------------------------------------------------
# run_all: a crash routes to the infra channel, never to a fabricated 0.0
# ---------------------------------------------------------------------------

def run_task(tmp_path, artifacts, ground_truth=None):
    """A full run over `artifacts`, behind one checkpoint that scores 1.0."""
    task_dir = tmp_path / "task"
    checkpoint = make_passing_checkpoint(task_dir)
    task = make_task(artifacts, ground_truth, checkpoints=[checkpoint])
    runner = CheckpointRunner(
        task=task, task_dir=task_dir, workspace=write_answer_workspace(tmp_path)
    )
    return runner.run_all(output_path=tmp_path / "reward.txt")


class TestRunAllCrashRouting:
    @pytest.mark.parametrize(
        "artifact_type, cause",
        [
            (CRASH_TYPE, "artifact_validator_crash"),
            (UNREGISTERED_TYPE, "validator_unregistered"),
        ],
    )
    def test_non_gated_keeps_checkpoint_total(
        self, tmp_path, crashing_validators, artifact_type, cause
    ):
        """Without the grounding gate, artifact validity carries no score
        weight: the checkpoint total stands and the artifact is only marked
        not-checked — the run is recorded, not lost or re-run."""
        result = run_task(tmp_path, [artifact_type])

        assert result.verifier_infra_error is None
        assert result.total_score == 1.0
        (artifact,) = result.artifact_results
        assert_infra_result(artifact, cause)

    @pytest.mark.parametrize(
        "artifact_type, cause",
        [
            (GROUNDED_CRASH_TYPE, "artifact_validator_crash"),
            (UNREGISTERED_TYPE, "validator_unregistered"),
            ("runbook", "groundedness_unsupported"),
        ],
    )
    def test_gated_routes_to_rerun_channel_not_the_gate(
        self, tmp_path, crashing_validators, artifact_type, cause
    ):
        """Under the gate, 'not checked' is indistinguishable from 'bad', so
        the run must be invalidated into the re-run channel — the gate reading
        the placeholder valid=False would blame the agent for a harness fault
        no agent output could have avoided."""
        result = run_task(tmp_path, [artifact_type], GROUNDED)

        assert result.score_gates == []
        assert result.verifier_infra_error is not None
        assert result.verifier_infra_error["cause"] == cause
        # total_score is a placeholder on an invalid run, never a measurement.
        assert result.total_score == 0.0
        # The checkpoint results survived the fault.
        assert result.checkpoint_results[0].score == 1.0

    def test_gated_crash_skips_gate_even_with_genuine_failure(
        self, tmp_path, crashing_validators
    ):
        """A genuinely failing artifact alongside a crash still yields an
        invalid run with the gate skipped: no score exists for the gate to
        force down, and the re-run re-judges everything."""
        workspace = write_answer_workspace(tmp_path)
        # Make the answer artifact genuinely fail groundedness: its citation
        # names a span that appears in no workspace file.
        answer = {
            "source_files": [{"path": "repo-a/src/pool.py"}],
            "citations": [
                {
                    "repo": "repo-a",
                    "file": "src/pool.py",
                    "evidence_span": "this span was fabricated and appears in no file",
                }
            ],
        }
        (workspace / "answer.json").write_text(json.dumps(answer))

        task_dir = tmp_path / "task"
        checkpoint = make_passing_checkpoint(task_dir)
        task = make_task(
            ["answer", GROUNDED_CRASH_TYPE], GROUNDED, checkpoints=[checkpoint]
        )
        runner = CheckpointRunner(task=task, task_dir=task_dir, workspace=workspace)
        result = runner.run_all(output_path=tmp_path / "reward.txt")

        assert result.score_gates == []
        assert result.total_score == 0.0
        assert result.verifier_infra_error["cause"] == "artifact_validator_crash"

    def test_non_gated_summary_reports_not_checked_never_invalid(
        self, tmp_path, crashing_validators
    ):
        result = run_task(tmp_path, [CRASH_TYPE])

        summary = result.summary()
        assert f"- {CRASH_TYPE}: INFRA_ERROR (not checked)" in summary
        assert "total_score: 1.0000" in summary
        # Neither the run nor the artifact may read as an agent failure.
        assert "INVALID" not in summary
        reward = (tmp_path / "reward.txt").read_text()
        assert "INFRA_ERROR (not checked)" in reward

    def test_gated_summary_reports_invalid_run(self, tmp_path, crashing_validators):
        result = run_task(tmp_path, [GROUNDED_CRASH_TYPE], GROUNDED)

        summary = result.summary()
        assert "total_score: INVALID" in summary
        assert f"- {GROUNDED_CRASH_TYPE}: INFRA_ERROR (not checked)" in summary
        assert "score_gate" not in summary


# ---------------------------------------------------------------------------
# CLI call sites
# ---------------------------------------------------------------------------

def write_task_toml(task_dir: Path, artifact_type: str, gated: bool) -> Path:
    task_dir.mkdir(parents=True, exist_ok=True)
    (task_dir / "check_pass.sh").write_text(
        '#!/bin/bash\necho \'{"score": 1.0, "detail": "ok"}\'\n'
    )
    ground_truth = (
        "\n[ground_truth]\nrequire_grounded_citations = true\n" if gated else ""
    )
    task_path = task_dir / "task.toml"
    task_path.write_text(
        f"""
[task]
id = "crash-cli-001"
suite = "incident_response"
difficulty = "medium"
session_type = "single"

[[checkpoints]]
name = "cp1"
weight = 1.0
verifier = "check_pass.sh"

[artifacts]
required = ["{artifact_type}"]
{ground_truth}"""
    )
    return task_path


class TestCliGuards:
    def test_validate_artifact_crash_exits_2(
        self, tmp_path, crashing_validators, capsys
    ):
        rc = cli_main(["validate-artifact", CRASH_TYPE, str(tmp_path)])

        assert rc == 2
        captured = capsys.readouterr()
        assert f"{CRASH_TYPE}: INFRA_ERROR (not checked)" in captured.out
        assert "KeyError" in captured.err

    def test_validate_artifact_invalid_still_exits_1(self, tmp_path, capsys):
        # Regression: a genuinely invalid artifact keeps exit code 1.
        workspace = tmp_path / "ws"
        workspace.mkdir()
        rc = cli_main(["validate-artifact", "answer", str(workspace)])

        assert rc == 1
        assert "INVALID" in capsys.readouterr().out

    def _run_cli(self, tmp_path, artifact_type: str, gated: bool) -> int:
        task_path = write_task_toml(tmp_path / "task", artifact_type, gated=gated)
        workspace = write_answer_workspace(tmp_path)
        return cli_main(
            [
                "run",
                str(task_path),
                "--workspace",
                str(workspace),
                "--output",
                str(tmp_path / "reward.txt"),
            ]
        )

    def test_run_gated_crash_exits_2(self, tmp_path, crashing_validators):
        """Exit 2 (no score exists, re-run), never 1 (agent failed)."""
        rc = self._run_cli(tmp_path, GROUNDED_CRASH_TYPE, gated=True)

        assert rc == 2

    def test_run_non_gated_crash_keeps_score_exit_0(
        self, tmp_path, crashing_validators
    ):
        """Non-gated, the crash is recorded on the artifact but the run's
        checkpoint score stands — exit 0, no wasted re-run."""
        rc = self._run_cli(tmp_path, CRASH_TYPE, gated=False)

        assert rc == 0


