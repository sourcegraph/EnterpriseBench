"""No-verdict rule for the Python scoring path (CheckpointRunner).

The sibling of test_scorer_guard.py, which covers the aggregate test.sh path.
Same invariant, other entry point: a verifier that never reached a verdict must
surface as an InfraError and route to the re-run channel — never as a
fabricated 1.0 (over-credit) or 0.0 (false zero).

Bead kyo34. The bug these lock down: runner.py read `exit 0` as score 1.0 and
`exit 1` as score 0.0 whenever the verifier emitted no parseable JSON, so a
verifier that crashed before scoring anything was handed full credit, and a
broken harness was blamed on the agent.
"""

from __future__ import annotations

import json
import stat
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(REPO_ROOT / "lib"))

from eb_verify.runner import CheckpointRunner  # noqa: E402
from eb_verify.scorer_guard import (  # noqa: E402
    NO_VERDICT_REASON,
    InfraError,
    guard_checkpoint_verdict,
)
from eb_verify.task_parser import (  # noqa: E402
    ArtifactSpec,
    Checkpoint,
    TaskDefinition,
)


def make_task(checkpoints: list | None = None) -> TaskDefinition:
    return TaskDefinition(
        id="kyo34-001",
        suite="customer_escalation",
        difficulty="medium",
        session_type="single",
        repos=[],
        checkpoints=checkpoints or [],
        artifacts=ArtifactSpec(),
    )


def write_verifier(task_dir: Path, name: str, body: str) -> str:
    """Write an executable check script; return its task-relative path."""
    path = task_dir / "checks" / name
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(body)
    path.chmod(path.stat().st_mode | stat.S_IEXEC | stat.S_IXGRP | stat.S_IXOTH)
    return f"checks/{name}"


@pytest.fixture
def dirs(tmp_path):
    task_dir = tmp_path / "task"
    task_dir.mkdir()
    workspace = tmp_path / "ws"
    workspace.mkdir()
    return task_dir, workspace


def run_one(task_dir: Path, workspace: Path, verifier: str, weight: float = 1.0):
    cp = Checkpoint(name="cp1", weight=weight, verifier=verifier, timeout_seconds=30)
    runner = CheckpointRunner(
        task=make_task([cp]), task_dir=task_dir, workspace=workspace
    )
    return runner, cp, runner.run_checkpoint(cp)


# ---------------------------------------------------------------------------
# guard_checkpoint_verdict — the rule itself
# ---------------------------------------------------------------------------

class TestGuardCheckpointVerdict:
    def test_valid_json_verdict_passes_through(self):
        verdict = guard_checkpoint_verdict(
            json.dumps({"score": 0.5, "detail": "partial"}), 0
        )
        assert not isinstance(verdict, InfraError)
        assert verdict["score"] == 0.5

    def test_score_only_verdict_is_valid(self):
        """22 active verifiers + the topological_order plugin emit `score`
        with no `passed` field. Requiring `passed` would infra-error them all."""
        verdict = guard_checkpoint_verdict(json.dumps({"score": 1.0}), 0)
        assert not isinstance(verdict, InfraError)

    @pytest.mark.parametrize("returncode", [0, 1])
    def test_empty_output_is_never_a_score(self, returncode):
        """The core fabrication: exit 0 -> 1.0, exit 1 -> 0.0. Both are lies."""
        result = guard_checkpoint_verdict("", returncode)
        assert isinstance(result, InfraError)
        assert result.reason == NO_VERDICT_REASON
        assert result.context["cause"] == "empty_output"

    def test_non_json_output_is_infra_not_a_score(self):
        result = guard_checkpoint_verdict("PASS", 0)
        assert isinstance(result, InfraError)
        assert result.context["cause"] == "malformed_output"

    def test_json_without_score_field_is_infra(self):
        result = guard_checkpoint_verdict(json.dumps({"detail": "no score"}), 0)
        assert isinstance(result, InfraError)
        assert result.context["cause"] == "no_score_field"

    def test_non_numeric_score_is_infra(self):
        result = guard_checkpoint_verdict(json.dumps({"score": "high"}), 0)
        assert isinstance(result, InfraError)
        assert result.context["cause"] == "non_numeric_score"

    def test_json_array_is_infra(self):
        result = guard_checkpoint_verdict(json.dumps([{"score": 1.0}]), 0)
        assert isinstance(result, InfraError)
        assert result.context["cause"] == "malformed_output"

    # -- the free-1.0 vectors: values that a naive max(0,min(1,float(x))) clamp
    # -- silently converts into FULL CREDIT. Each was empirically confirmed to
    # -- score 1.0 before is_valid_score existed (bead kyo34 review).
    @pytest.mark.parametrize(
        "raw,label",
        [
            ('{"score": NaN}', "nan"),
            ('{"score": Infinity}', "infinity"),
            ('{"score": -Infinity}', "negative-infinity"),
            ('{"score": true}', "bool-true"),
            ('{"score": false}', "bool-false"),
            ('{"score": 999}', "out-of-range-high"),
            ('{"score": -5}', "out-of-range-low"),
            ('{"score": "1.0"}', "string-numeric"),
        ],
    )
    def test_non_score_values_never_become_a_score(self, raw, label):
        """json.loads parses bare NaN/Infinity by default, and float(True)==1.0.
        Clamping any of them yields 1.0 — a verifier that divided by zero, or
        meant 'passed', would score full marks."""
        result = guard_checkpoint_verdict(raw, 0)
        assert isinstance(result, InfraError), f"{label} was accepted as a score"
        assert result.context["cause"] == "non_numeric_score"

    def test_float_noise_at_the_bounds_is_still_a_score(self):
        """A verifier computing round(hits/total, 2) may land a hair outside
        [0,1]. That's a real verdict, not a broken verifier."""
        for raw in ('{"score": 1.0000001}', '{"score": -0.0000001}'):
            assert not isinstance(guard_checkpoint_verdict(raw, 0), InfraError)

    def test_harness_import_failure_reported_from_stderr(self):
        """The live httpx regression signature (bead ssikq): empty stdout,
        exit 1, `No module named 'eb_verify'` on stderr. Reported as the import
        failure it is, not a bare 'no output'."""
        result = guard_checkpoint_verdict(
            "", 1, stderr="ModuleNotFoundError: No module named 'eb_verify'"
        )
        assert isinstance(result, InfraError)
        assert result.context["cause"] == "harness_import_failure"

    def test_task_subject_import_error_is_not_misread_as_harness_failure(self):
        """An error-provenance task whose SUBJECT raises ModuleNotFoundError
        still scores normally — the guard keys on OUR module name only."""
        verdict = guard_checkpoint_verdict(
            json.dumps({"score": 1.0, "detail": "No module named 'requests'"}), 0
        )
        assert not isinstance(verdict, InfraError)


# ---------------------------------------------------------------------------
# run_checkpoint — the fabrication paths this bead removes
# ---------------------------------------------------------------------------

class TestRunCheckpointNoFabrication:
    def test_exit_0_without_json_is_not_a_free_1_0(self, dirs):
        """OVER-CREDIT. The worst case: a verifier that crashed before printing
        anything, exiting 0, was handed a perfect score."""
        task_dir, workspace = dirs
        v = write_verifier(task_dir, "silent_ok.sh", "#!/bin/bash\nexit 0\n")
        _, _, result = run_one(task_dir, workspace, v)

        assert result.infra_error is not None
        assert result.infra_error.reason == NO_VERDICT_REASON
        assert result.score != 1.0

    def test_exit_nonzero_without_json_is_not_a_real_zero(self, dirs):
        """FALSE ZERO. The agent gets blamed for a broken verifier."""
        task_dir, workspace = dirs
        v = write_verifier(task_dir, "silent_fail.sh", "#!/bin/bash\nexit 1\n")
        _, _, result = run_one(task_dir, workspace, v)

        assert result.infra_error is not None
        assert result.passed is False

    def test_missing_verifier_is_infra_not_a_zero(self, dirs):
        task_dir, workspace = dirs
        _, _, result = run_one(task_dir, workspace, "checks/nonexistent.sh")

        assert result.infra_error is not None
        assert result.infra_error.context["cause"] == "missing_verifier"

    def test_path_escape_is_infra_not_a_zero(self, dirs):
        task_dir, workspace = dirs
        _, _, result = run_one(task_dir, workspace, "../../../etc/passwd")

        assert result.infra_error is not None
        assert result.infra_error.context["cause"] == "path_escape"

    def test_real_verdict_still_scores(self, dirs):
        """The guard must not break the 400 verifiers that do emit JSON."""
        task_dir, workspace = dirs
        v = write_verifier(
            task_dir,
            "good.sh",
            '#!/bin/bash\necho \'{"score": 0.75, "detail": "ok"}\'\n',
        )
        _, _, result = run_one(task_dir, workspace, v)

        assert result.infra_error is None
        assert result.score == 0.75
        assert result.passed is True

    def test_legitimate_zero_still_scores_zero(self, dirs):
        """A verifier that RAN and judged the agent wrong is a real 0.0 — it
        must NOT be laundered into an infra error."""
        task_dir, workspace = dirs
        v = write_verifier(
            task_dir,
            "judged_fail.sh",
            '#!/bin/bash\necho \'{"score": 0.0, "detail": "wrong answer"}\'\nexit 1\n',
        )
        _, _, result = run_one(task_dir, workspace, v)

        assert result.infra_error is None
        assert result.score == 0.0
        assert result.passed is False

    def test_httpx_import_failure_regression(self, dirs):
        """Built on the real signature of the two active customer_escalation
        tasks that exec a nonexistent eb_verify.plugins.file_extraction module
        (bead ssikq): they were silently recording a legitimate 0.0 on a
        weight-0.40 checkpoint. They must now fail closed."""
        task_dir, workspace = dirs
        v = write_verifier(
            task_dir,
            "check_error_source.sh",
            "#!/bin/bash\npython3 -m eb_verify.plugins.file_extraction\n",
        )
        _, _, result = run_one(task_dir, workspace, v, weight=0.40)

        assert result.infra_error is not None
        assert result.infra_error.reason == NO_VERDICT_REASON
        assert result.score != 1.0


# ---------------------------------------------------------------------------
# run_all — an infra checkpoint must invalidate the whole run
# ---------------------------------------------------------------------------

class TestRunAllRefusesFabricatedTotal:
    def test_infra_checkpoint_invalidates_total(self, dirs, tmp_path):
        """Even one no-verdict checkpoint means the run has no valid total.
        Averaging its placeholder 0.0 against real scores would produce a
        plausible number that nobody could tell apart from a real one."""
        task_dir, workspace = dirs
        good = write_verifier(
            task_dir, "good.sh", '#!/bin/bash\necho \'{"score": 1.0}\'\n'
        )
        broken = write_verifier(task_dir, "broken.sh", "#!/bin/bash\nexit 0\n")

        task = make_task(
            [
                Checkpoint(name="good", weight=0.6, verifier=good, timeout_seconds=30),
                Checkpoint(
                    name="broken", weight=0.4, verifier=broken, timeout_seconds=30
                ),
            ]
        )
        runner = CheckpointRunner(task=task, task_dir=task_dir, workspace=workspace)
        result = runner.run_all(output_path=tmp_path / "reward.txt")

        assert result.verifier_infra_error is not None
        assert result.verifier_infra_error["reason"] == NO_VERDICT_REASON

    def test_reward_txt_is_not_parseable_as_a_number_when_invalid(
        self, dirs, tmp_path
    ):
        """reward.txt is the machine-read artifact. On an invalid run it must
        NOT carry a numeric total_score any text parser could bank as real."""
        task_dir, workspace = dirs
        broken = write_verifier(task_dir, "broken.sh", "#!/bin/bash\nexit 0\n")
        task = make_task(
            [Checkpoint(name="cp1", weight=1.0, verifier=broken, timeout_seconds=30)]
        )
        runner = CheckpointRunner(task=task, task_dir=task_dir, workspace=workspace)

        reward = tmp_path / "reward.txt"
        runner.run_all(output_path=reward)

        text = reward.read_text()
        assert "total_score: INVALID" in text
        assert "total_score: 1.0" not in text
        assert "total_score: 0.0" not in text

    def test_infra_checkpoint_line_carries_no_score(self, dirs, tmp_path):
        """The no-score rule holds at the ELEMENT, not just the aggregate.

        Guarding only `total_score` still left the per-checkpoint line printing
        the placeholder as `FAIL (score=0.00)` — a false zero attributable to
        the agent, one level down, in the same machine-read artifact.
        """
        task_dir, workspace = dirs
        good = write_verifier(
            task_dir, "good.sh", '#!/bin/bash\necho \'{"score": 1.0}\'\n'
        )
        broken = write_verifier(task_dir, "broken.sh", "#!/bin/bash\nexit 0\n")
        task = make_task(
            [
                Checkpoint(name="good", weight=0.6, verifier=good, timeout_seconds=30),
                Checkpoint(
                    name="broken", weight=0.4, verifier=broken, timeout_seconds=30
                ),
            ]
        )
        runner = CheckpointRunner(task=task, task_dir=task_dir, workspace=workspace)

        reward = tmp_path / "reward.txt"
        runner.run_all(output_path=reward)

        lines = reward.read_text().splitlines()
        broken_line = next(ln for ln in lines if ln.startswith("  - broken:"))
        assert "INFRA_ERROR" in broken_line
        assert "no score" in broken_line
        assert "score=" not in broken_line  # neither a free 1.00 nor a false 0.00

        # The checkpoint that DID reach a verdict still reports its real score.
        good_line = next(ln for ln in lines if ln.startswith("  - good:"))
        assert "PASS (score=1.00" in good_line

    def test_cli_run_exits_2_on_no_verdict(self, dirs, tmp_path, monkeypatch, capsys):
        """Exit 2 == 'this run produced no score'. A caller that reads any
        nonzero exit as 'the agent failed' would otherwise bank a broken
        harness as a real 0.0 — which is the whole bug."""
        from eb_verify import cli

        task_dir, workspace = dirs
        write_verifier(task_dir, "broken.sh", "#!/bin/bash\nexit 0\n")
        (task_dir / "task.toml").write_text(
            "[task]\n"
            'id = "kyo34-cli"\n'
            'suite = "customer_escalation"\n'
            'difficulty = "medium"\n'
            'session_type = "single"\n'
            "\n"
            "[[checkpoints]]\n"
            'name = "cp1"\n'
            "weight = 1.0\n"
            'verifier = "checks/broken.sh"\n'
        )

        rc = cli.main(
            [
                "run",
                str(task_dir / "task.toml"),
                "--workspace",
                str(workspace),
                "--output",
                str(tmp_path / "reward.txt"),
            ]
        )

        assert rc == 2, "no-verdict run must not exit 0 (pass) or 1 (agent failed)"

    def test_clean_run_still_reports_a_real_total(self, dirs, tmp_path):
        """No regression: a run where every verifier reached a verdict still
        produces a normal numeric total."""
        task_dir, workspace = dirs
        good = write_verifier(
            task_dir, "good.sh", '#!/bin/bash\necho \'{"score": 0.5}\'\n'
        )
        task = make_task(
            [Checkpoint(name="cp1", weight=1.0, verifier=good, timeout_seconds=30)]
        )
        runner = CheckpointRunner(task=task, task_dir=task_dir, workspace=workspace)
        result = runner.run_all(output_path=tmp_path / "reward.txt")

        assert result.verifier_infra_error is None
        assert result.total_score == 0.5
        assert "total_score: 0.5000" in (tmp_path / "reward.txt").read_text()


# ---------------------------------------------------------------------------
# guard_verifier_output — the OTHER entry point (production docker/test.sh)
#
# scorer_guard's docstring promises the invariant is "enforced identically" at
# every scoring entry point. It wasn't: guard_checkpoint_verdict validated the
# score, its sibling didn't. run_task sums `score * weight` with NO clamp, so a
# test.sh emitting NaN/Infinity wrote a nan/inf task_score into published
# results. Confirmed empirically before the fix (bead kyo34 review).
# ---------------------------------------------------------------------------

from eb_verify.scorer_guard import guard_verifier_output  # noqa: E402


class TestProductionPathScoreValidity:
    @pytest.mark.parametrize(
        "score_token,label",
        [
            ("NaN", "nan"),
            ("Infinity", "infinity"),
            ("999", "out-of-range"),
            ("true", "bool"),
        ],
    )
    def test_invalid_checkpoint_score_is_infra_not_a_task_score(
        self, score_token, label
    ):
        # verifier_ran=true so the checkpoint clears the attestation gate (bead
        # glka.2) and this test actually exercises the score-validity check it
        # pins — without it the gate would fire first and mask a broken score.
        stdout = (
            '{"checkpoints": [{"name": "x", "verifier_ran": true, "score": '
            + score_token
            + ', "weight": 1.0}]}'
        )
        result = guard_verifier_output(stdout, 0)
        assert isinstance(result, InfraError), f"{label} reached task_score"

    def test_real_scores_still_pass_through(self):
        # verifier_ran=true is the per-checkpoint attestation test_runner.sh (the
        # sole producer of this JSON) now emits for every verifier that reached a
        # verdict (bead glka.2). A real scores payload always carries it; its
        # absence is what routes to an infra error.
        stdout = (
            '{"checkpoints": [{"name": "a", "score": 0.5, "weight": 0.5, "verifier_ran": true},'
            ' {"name": "b", "score": 0.0, "weight": 0.5, "verifier_ran": true}]}'
        )
        result = guard_verifier_output(stdout, 0)
        assert not isinstance(result, InfraError)
        assert len(result["checkpoints"]) == 2
