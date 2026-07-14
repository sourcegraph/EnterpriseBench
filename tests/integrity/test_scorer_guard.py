"""Scorer trust boundary — guard_verifier_output + code_patch infra vectors.

These vectors exercise the single score-integrity invariant directly (no docker):
a score is valid only if the pristine verifier ran on real agent output; any
infra/verifier failure surfaces as an InfraError / infra sentinel, never a 0.0.
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(REPO_ROOT / "lib"))

from eb_verify.scorer_guard import (  # noqa: E402
    INFRA_SENTINEL,
    DiffProbeError,
    InfraError,
    guard_verifier_output,
    is_valid_score,
)
from eb_verify.plugins.code_patch import CodePatchValidator  # noqa: E402


# ---------------------------------------------------------------------------
# is_valid_score — the shared predicate behind every scoring entry point
# ---------------------------------------------------------------------------


class TestIsValidScore:
    @pytest.mark.parametrize("value", [0.0, 0.5, 1.0, 0, 1, 1.0 + 1e-9, -1e-9])
    def test_real_scores_and_bound_slop_accepted(self, value: object) -> None:
        assert is_valid_score(value) is True

    @pytest.mark.parametrize(
        "value",
        [
            float("nan"),  # min(1.0, nan) is 1.0 in CPython → a naive clamp gives full marks
            float("inf"),
            float("-inf"),
            True,          # float(True) == 1.0 → {"score": true} gives full marks
            False,
            999,
            1.5,
            -0.3,
            "1.0",         # a string is not a score, even when it parses
            "abc",
            None,
            object(),
        ],
    )
    def test_non_scores_rejected(self, value: object) -> None:
        assert is_valid_score(value) is False


# ---------------------------------------------------------------------------
# guard_verifier_output — deterministic-stage vectors (under-credit direction)
# ---------------------------------------------------------------------------


def _cp(**fields) -> dict:
    """A checkpoint as test_runner.sh emits it.

    ``verifier_ran`` defaults to True — "the verifier reached a verdict" — so
    each test states only the property it is actually about. Tests that are
    specifically about the attestation override it explicitly.
    """
    return {"name": "cp1", "score": 0.0, "passed": False, "verifier_ran": True, **fields}


class TestGuardVerifierOutput:
    def test_empty_output_is_infra_not_zero(self) -> None:
        """broken-test.sh-false-zero (apfp #2): empty stdout is an infra error,
        never a legitimate all-checkpoint-fail 0.0."""
        out = guard_verifier_output("", returncode=1)
        assert isinstance(out, InfraError)
        assert out.reason == "empty_verifier_output"
        assert out.stage == "deterministic_scoring"

    def test_whitespace_only_output_is_infra(self) -> None:
        out = guard_verifier_output("   \n  ", returncode=0)
        assert isinstance(out, InfraError)
        assert out.reason == "empty_verifier_output"

    def test_non_json_output_is_infra(self) -> None:
        """malformed-verifier-output: a crash traceback on stdout is infra."""
        out = guard_verifier_output("Traceback (most recent call last): boom", returncode=1)
        assert isinstance(out, InfraError)
        assert out.reason == "malformed_verifier_output"

    def test_non_object_json_is_infra(self) -> None:
        out = guard_verifier_output("[1, 2, 3]", returncode=0)
        assert isinstance(out, InfraError)
        assert out.reason == "malformed_verifier_output"

    def test_top_level_error_key_is_infra(self) -> None:
        """verifier-reported-error: test.sh emits {task_score:0.0, error:...}
        for 'no .verifiers/ directory' / 'cannot access repo' — previously read
        by no caller, so it became a false 0.0."""
        payload = json.dumps(
            {"task_score": 0.0, "all_passed": False, "error": "No .verifiers/ directory found"}
        )
        out = guard_verifier_output(payload, returncode=1)
        assert isinstance(out, InfraError)
        assert out.reason == "verifier_reported_error"

    # --- primary gate: positive attestation (bead glka.2) -------------------
    #
    # These fire on the ABSENCE of "the verifier reached a verdict", so an
    # unrecognised never-ran mode fails closed instead of becoming a false 0.0.

    def test_missing_attestation_is_infra_not_zero(self) -> None:
        """A checkpoint that cannot attest it ran is refused a score — this is
        what makes an unknown future never-ran mode fail closed."""
        payload = json.dumps(
            {
                "task_score": 0.0,
                "all_passed": False,
                "checkpoints": [{"name": "cp1", "score": 0.0, "passed": False, "detail": "x"}],
            }
        )
        out = guard_verifier_output(payload, returncode=1)
        assert isinstance(out, InfraError)
        assert out.reason == "verifier_did_not_run"
        assert out.context["checkpoint"] == "cp1"

    def test_false_attestation_is_infra_not_zero(self) -> None:
        """The silent variant: the verifier swallowed a missing interpreter and
        printed a well-formed 0.0 with exit 0. Only the attestation catches it —
        there is nothing in the score, the exit code, or the detail to key on."""
        payload = json.dumps(
            {
                "task_score": 0.0,
                "all_passed": False,
                "checkpoints": [_cp(verifier_ran=False, exit_code=0, detail="")],
            }
        )
        out = guard_verifier_output(payload, returncode=0)
        assert isinstance(out, InfraError)
        assert out.reason == "verifier_did_not_run"

    def test_docker_cp_module_not_found_is_infra(self) -> None:
        """docker-cp-module-not-found (bead hktt/pt0n): a checkpoint whose
        detail carries the harness-import failure is infra, not a real 0.

        Attested as having run, so this proves the tertiary signature net still
        fires on its own rather than being masked by the primary gate."""
        payload = json.dumps(
            {
                "task_score": 0.0,
                "all_passed": False,
                "checkpoints": [
                    _cp(
                        name="error_source",
                        detail="ModuleNotFoundError: No module named 'eb_verify.plugins'",
                    )
                ],
            }
        )
        out = guard_verifier_output(payload, returncode=0)
        assert isinstance(out, InfraError)
        assert out.reason == "verifier_crash"
        assert out.context["checkpoint"] == "error_source"

    def test_explicit_sentinel_in_checkpoint_detail_is_infra(self) -> None:
        payload = json.dumps(
            {
                "task_score": 0.0,
                "checkpoints": [_cp(detail=f"{INFRA_SENTINEL}: git probe failed")],
            }
        )
        out = guard_verifier_output(payload, returncode=0)
        assert isinstance(out, InfraError)
        assert out.reason == "verifier_crash"

    # --- negative controls: the guard must NOT over-flag legitimate results ---

    def test_valid_scores_pass_through(self) -> None:
        payload = json.dumps(
            {
                "task_score": 0.75,
                "all_passed": False,
                "checkpoints": [_cp(score=0.75, passed=True, detail="ok")],
            }
        )
        out = guard_verifier_output(payload, returncode=0)
        assert isinstance(out, dict)
        assert out["task_score"] == 0.75

    def test_import_error_in_task_subject_is_not_flagged(self) -> None:
        """False-positive guard: an error-provenance TASK whose subject is an
        agent-reported ImportError must score normally — the guard only matches
        harness-specific signatures, never a generic 'ImportError' string."""
        payload = json.dumps(
            {
                "task_score": 0.0,
                "all_passed": False,
                "checkpoints": [
                    _cp(
                        name="root_cause",
                        detail="agent answer wrong: expected ImportError in requests.compat",
                    )
                ],
            }
        )
        out = guard_verifier_output(payload, returncode=1)
        assert isinstance(out, dict), "generic ImportError in a task subject must not be infra"
        assert out["task_score"] == 0.0

    def test_genuine_all_fail_zero_passes_through(self) -> None:
        """A real all-checkpoint-fail 0.0 (verifier ran, agent failed) is a
        legitimate score and must pass through, not become an infra error."""
        payload = json.dumps(
            {
                "task_score": 0.0,
                "all_passed": False,
                "checkpoints": [_cp(detail="assertion failed")],
            }
        )
        out = guard_verifier_output(payload, returncode=1)
        assert isinstance(out, dict)
        assert out["task_score"] == 0.0


class TestInfraErrorShape:
    def test_as_verifier_error_shape(self) -> None:
        err = InfraError(
            reason="empty_verifier_output",
            stage="deterministic_scoring",
            detail="test.sh produced no output",
            context={"returncode": 137},
        )
        d = err.as_verifier_error()
        assert d["reason"] == "empty_verifier_output"
        assert d["stage"] == "deterministic_scoring"
        assert d["detail"] == "test.sh produced no output"
        assert d["returncode"] == 137


# ---------------------------------------------------------------------------
# code_patch — git-error-false-no-changes vector (apfp #4, under-credit)
# ---------------------------------------------------------------------------


def _git(repo: Path, *args: str) -> None:
    subprocess.run(
        ["git", *args],
        cwd=str(repo),
        check=True,
        capture_output=True,
        text=True,
        env={
            **__import__("os").environ,
            "GIT_AUTHOR_NAME": "t",
            "GIT_AUTHOR_EMAIL": "t@t",
            "GIT_COMMITTER_NAME": "t",
            "GIT_COMMITTER_EMAIL": "t@t",
        },
    )


class TestCodePatchInfra:
    def test_git_probe_failure_surfaces_infra_sentinel(self, tmp_path: Path) -> None:
        """A repo dir with a corrupt/empty .git makes `git diff` exit non-zero.
        The validator must surface the infra sentinel, NOT 'No code changes'."""
        repo = tmp_path / "workspace" / "broken-repo"
        (repo / ".git").mkdir(parents=True)  # passes the .git-exists check, git diff fails
        result = CodePatchValidator().validate(tmp_path / "workspace")
        assert result.valid is False
        assert INFRA_SENTINEL in result.detail
        assert "No code changes" not in result.detail

    def test_clean_repo_no_changes_is_not_infra(self, tmp_path: Path) -> None:
        """Negative control: a genuinely clean repo is a real no-changes result,
        NOT an infra error — the sentinel must be absent."""
        ws = tmp_path / "workspace"
        repo = ws / "clean-repo"
        repo.mkdir(parents=True)
        _git(repo, "init", "-q")
        (repo / "f.txt").write_text("hello\n")
        _git(repo, "add", "-A")
        _git(repo, "commit", "-qm", "init")
        result = CodePatchValidator().validate(ws)
        assert result.valid is False
        assert INFRA_SENTINEL not in result.detail
        assert "No code changes" in result.detail

    def test_repo_with_changes_is_valid(self, tmp_path: Path) -> None:
        """Negative control: a repo with real uncommitted changes validates."""
        ws = tmp_path / "workspace"
        repo = ws / "changed-repo"
        repo.mkdir(parents=True)
        _git(repo, "init", "-q")
        (repo / "f.txt").write_text("hello\n")
        _git(repo, "add", "-A")
        _git(repo, "commit", "-qm", "init")
        (repo / "f.txt").write_text("hello\nworld\n")
        result = CodePatchValidator().validate(ws)
        assert result.valid is True
        assert INFRA_SENTINEL not in result.detail
        assert "changed-repo" in result.detail


def test_diff_probe_error_is_raised_on_bad_repo(tmp_path: Path) -> None:
    """Unit-level: the probe RAISES (does not return None/0) on a git failure."""
    from eb_verify.plugins.code_patch import _get_diff_stat, _get_diff_lines

    bad = tmp_path / "bad"
    (bad / ".git").mkdir(parents=True)
    with pytest.raises(DiffProbeError):
        _get_diff_stat(bad)
    with pytest.raises(DiffProbeError):
        _get_diff_lines(bad)
