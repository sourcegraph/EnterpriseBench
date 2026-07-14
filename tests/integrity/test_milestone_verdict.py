"""Milestone scoring must never invent a number for a verifier that did not score.

The third runner (bead chc2z). ``lib/eb_verify/runner.py`` (kyo34) and
``scripts/sandbox/test_runner.sh`` (glka.2) already stopped fabricating scores
from exit codes; ``scripts/orchestration/milestone.py`` still did, and it is
wired to production for every ``session_type = "chain"`` task.

The two fabrications under test, both reachable from a real chain run:

* exit 0 + no verdict  -> a free 1.0 (over-credit: full marks for a verifier
  that crashed before scoring anything);
* exit 1 + no verdict  -> a false 0.0 (blames the agent for a broken harness).

Neither is a score. Both must surface as an ``InfraError`` that propagates all
the way to ``chain_result.json``, which must not carry a numeric ``total_score``
for an invalid run.
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(REPO_ROOT / "scripts"))
sys.path.insert(0, str(REPO_ROOT / "lib"))

from eb_verify.scorer_guard import NO_VERDICT_REASON, InfraError
from orchestration.chain_runner import ChainResult, ChainTaskDefinition, _compute_total_score
from orchestration.milestone import (
    MilestoneResult,
    SessionScore,
    run_milestone_verifier,
)


def write_stub_verifier(tmp_path: Path, body: str, name: str = "check.sh") -> Path:
    """Write an executable stub verifier; return its absolute path.

    Distinct from ``test_checkpoint_verdict.write_verifier``, which writes into a
    task dir's ``checks/`` and returns a *task-relative* path. Named apart so the
    two are not mistaken for one another in this directory.
    """
    script = tmp_path / name
    script.write_text(body)
    script.chmod(0o755)
    return script


def run(verifier: Path, tmp_path: Path, **kw) -> MilestoneResult:
    return run_milestone_verifier(
        verifier_path=str(verifier),
        workspace_path=str(tmp_path),
        session_number=1,
        milestone_name="m1",
        **kw,
    )


class TestNoVerdictIsNeverAScore:
    """A verifier that never reached a verdict has no score — the core invariant."""

    def test_exit0_without_json_is_infra_not_a_free_1_0(self, tmp_path):
        """THE OVER-CREDIT. A verifier that printed nothing and exited 0 scored 1.0."""
        v = write_stub_verifier(tmp_path, "#!/bin/sh\nexit 0\n")

        result = run(v, tmp_path)

        assert result.infra_error is not None
        assert result.infra_error.reason == NO_VERDICT_REASON
        assert result.infra_error.context["cause"] == "empty_output"
        assert result.score != 1.0, "exit 0 with no verdict must not be full marks"
        assert result.score is None
        assert not result.passed

    def test_exit1_without_json_is_infra_not_a_false_0_0(self, tmp_path):
        """THE FALSE ZERO. A broken harness scored the agent 0.0 and looked legitimate."""
        v = write_stub_verifier(tmp_path, "#!/bin/sh\necho 'Traceback: boom' >&2\nexit 1\n")

        result = run(v, tmp_path)

        assert result.infra_error is not None
        assert result.infra_error.reason == NO_VERDICT_REASON
        assert result.score is None, "a broken harness must not be recorded as a 0.0"

    def test_missing_verifier_is_infra_not_a_0_0(self, tmp_path):
        result = run(tmp_path / "does_not_exist.sh", tmp_path)

        assert result.infra_error is not None
        assert result.infra_error.context["cause"] == "missing_verifier"
        assert result.score is None

    def test_timeout_is_infra_not_a_0_0(self, tmp_path):
        v = write_stub_verifier(tmp_path, "#!/bin/sh\nsleep 5\n")

        result = run(v, tmp_path, timeout_seconds=1)

        assert result.infra_error is not None
        assert result.infra_error.context["cause"] == "verifier_timeout"
        assert result.score is None

    def test_unexecutable_verifier_is_infra_not_a_crash(self, tmp_path):
        """subprocess.run raises OSError on a non-executable file — it must not
        take the whole chain down with it."""
        script = tmp_path / "noexec.sh"
        script.write_text("#!/bin/sh\necho '{\"score\": 1.0}'\n")
        script.chmod(0o644)  # readable, NOT executable

        result = run(script, tmp_path)

        assert result.infra_error is not None
        assert result.infra_error.context["cause"] == "exec_error"
        assert result.score is None


class TestMalformedVerdicts:
    """Valid JSON is not automatically a valid verdict."""

    def test_json_without_score_key_does_not_fall_back_to_exit_code(self, tmp_path):
        """The subtle one: stdout IS valid JSON, but carries no 'score'. The old
        `output.get("score", score)` silently fell back to the fabricated
        exit-code score — so exit 0 still bought a free 1.0."""
        v = write_stub_verifier(tmp_path, "#!/bin/sh\necho '{\"message\": \"ok\"}'\nexit 0\n")

        result = run(v, tmp_path)

        assert result.infra_error is not None
        assert result.infra_error.context["cause"] == "no_score_field"
        assert result.score != 1.0
        assert result.score is None

    def test_score_that_is_not_a_real_0_1_number_is_infra(self, tmp_path):
        """NaN is the representative over-credit: json.loads parses bare NaN, and
        min(1.0, nan) is nan -> a naive clamp hands out full marks.

        The full rejection table (Infinity, true, 999, -5, "1.0", ...) is asserted
        directly against ``guard_checkpoint_verdict`` in test_checkpoint_verdict.py.
        milestone.py has no per-literal logic — it delegates to the guard and
        branches once on the result — so this asserts the wiring, not the table.
        """
        v = write_stub_verifier(tmp_path, "#!/bin/sh\necho '{\"score\": NaN}'\nexit 0\n")

        result = run(v, tmp_path)

        assert result.infra_error is not None, "NaN is not a score"
        assert result.infra_error.context["cause"] == "non_numeric_score"
        assert result.score is None

    def test_non_json_stdout_is_infra(self, tmp_path):
        v = write_stub_verifier(tmp_path, "#!/bin/sh\necho 'PASS'\nexit 0\n")

        result = run(v, tmp_path)

        assert result.infra_error is not None
        assert result.score is None


class TestRealVerdictsPassThroughUnchanged:
    """The fix must not disturb the 3 real milestone verifiers, which emit a JSON
    score on every path (including their failure paths, with a nonzero exit)."""

    def test_valid_partial_score_survives(self, tmp_path):
        v = write_stub_verifier(
            tmp_path, "#!/bin/sh\necho '{\"score\": 0.6, \"message\": \"partial\"}'\nexit 0\n"
        )

        result = run(v, tmp_path)

        assert result.infra_error is None
        assert result.score == 0.6
        assert result.passed
        assert result.message == "partial"

    def test_valid_score_with_nonzero_exit_is_a_real_fail_not_infra(self, tmp_path):
        """check_investigation.sh emits {"score": 0.6} and exits 1. That is a
        genuine partial-credit FAIL verdict, not a broken harness: the score is
        honored and `passed` stays exit-code-derived."""
        v = write_stub_verifier(
            tmp_path, "#!/bin/sh\necho '{\"score\": 0.6, \"message\": \"partial\"}'\nexit 1\n"
        )

        result = run(v, tmp_path)

        assert result.infra_error is None
        assert result.score == 0.6
        assert not result.passed

    def test_zero_score_from_a_real_verdict_is_still_a_zero(self, tmp_path):
        """A legitimate 0.0 (verifier ran, agent failed) must survive. The point
        is to stop *inventing* zeros, not to stop recording earned ones."""
        v = write_stub_verifier(
            tmp_path, "#!/bin/sh\necho '{\"score\": 0.0, \"message\": \"no report\"}'\nexit 1\n"
        )

        result = run(v, tmp_path)

        assert result.infra_error is None
        assert result.score == 0.0
        assert not result.passed


class TestMilestoneResultInvariantIsStructural:
    """score and infra_error are mutually exclusive and jointly exhaustive.

    The aggregators multiply and sum `score` unguarded, so an ill-formed result is
    a crash (score=None, no error) or a laundered placeholder (both set). Neither
    may be constructible — the invariant is enforced, not just documented.
    """

    def test_neither_score_nor_error_is_rejected(self):
        with pytest.raises(ValueError, match="reached a verdict"):
            MilestoneResult(1, "m", passed=True, score=None)

    def test_both_score_and_error_is_rejected(self):
        with pytest.raises(ValueError, match="reached a verdict"):
            MilestoneResult(
                1,
                "m",
                passed=False,
                score=0.0,
                infra_error=InfraError(NO_VERDICT_REASON, "milestone", "boom"),
            )


class TestSessionScoreDoesNotAverageInPlaceholders:
    def test_total_score_is_none_when_any_milestone_is_infra(self):
        """A placeholder must never be averaged into a real mean: 1.0 and an
        invalid milestone is not a 0.5."""
        score = SessionScore(
            session_number=1,
            milestones=[
                MilestoneResult(1, "good", passed=True, score=1.0),
                MilestoneResult(
                    1,
                    "broken",
                    passed=False,
                    score=None,
                    infra_error=InfraError(NO_VERDICT_REASON, "milestone", "boom"),
                ),
            ],
        )

        assert score.total_score is None
        assert not score.all_passed

    def test_total_score_is_the_mean_when_every_milestone_scored(self):
        score = SessionScore(
            session_number=1,
            milestones=[
                MilestoneResult(1, "a", passed=True, score=1.0),
                MilestoneResult(1, "b", passed=False, score=0.5),
            ],
        )

        assert score.total_score == pytest.approx(0.75)


class TestChainResultCarriesNoNumberForAnInvalidRun:
    def _task_def(self) -> ChainTaskDefinition:
        return ChainTaskDefinition(
            task_id="t",
            suite="s",
            difficulty="d",
            session_count=1,
            repos=[],
            sessions=[],
            final_checkpoints=[{"name": "cp", "weight": 1.0, "verifier": "c.sh"}],
        )

    def test_infra_error_short_circuits_the_weighted_sum(self):
        infra = InfraError(NO_VERDICT_REASON, "milestone", "verifier did not run")
        chain = ChainResult(task_id="t", total_sessions=1)
        chain.milestone_scores = [
            SessionScore(
                session_number=1,
                milestones=[
                    MilestoneResult(1, "cp", passed=True, score=1.0),
                    MilestoneResult(1, "broken", passed=False, score=None, infra_error=infra),
                ],
            )
        ]

        _compute_total_score(chain, self._task_def())

        assert chain.total_score is None
        assert chain.final_score is None
        assert chain.verifier_infra_error is not None
        assert chain.verifier_infra_error["reason"] == NO_VERDICT_REASON

    def test_valid_run_still_computes_a_weighted_total(self):
        chain = ChainResult(task_id="t", total_sessions=1)
        chain.milestone_scores = [
            SessionScore(
                session_number=1,
                milestones=[MilestoneResult(1, "cp", passed=True, score=1.0)],
            )
        ]

        _compute_total_score(chain, self._task_def())

        assert chain.total_score == pytest.approx(1.0)
        assert chain.verifier_infra_error is None

    def test_summary_renders_invalid_run_instead_of_a_number(self):
        chain = ChainResult(task_id="t", total_sessions=1)
        chain.total_score = None
        chain.final_score = None
        chain.verifier_infra_error = InfraError(
            NO_VERDICT_REASON, "milestone", "boom"
        ).as_verifier_error()

        text = chain.summary()

        assert "INVALID RUN" in text
        assert "0.00" not in text, "an invalid run must not render as a zero score"

    def test_summary_of_an_unscored_chain_does_not_crash_or_invent_a_zero(self):
        """A chain with no milestone results at all has no score. It must render
        as unscored — not raise on the None, and not print the old fabricated
        '0.00' that came from the score fields defaulting to 0.0."""
        chain = ChainResult(task_id="t", total_sessions=1)

        text = chain.summary()

        assert "0.00" not in text
        assert "NOT SCORED" in text


class TestChainResultJsonContract:
    """End-to-end: the file a downstream reader parses must not carry a number."""

    def test_chain_result_json_total_score_is_null_and_exit_is_nonzero(self, tmp_path):
        task_toml = tmp_path / "task.toml"
        # The [[repos]] entry is load-bearing: the session has to SUCCEED for the
        # final checkpoint to run at all (a chain whose session failed is gated off
        # from its checkpoints — bead 6c9wp). Without it the simulated agent dies on
        # repos[0] and this would assert the broken verifier through a chain that
        # never ran, which is a different bug with a different channel.
        task_toml.write_text(
            '[task]\n'
            'id = "chain-infra-test"\n'
            'suite = "customer_escalation"\n'
            'difficulty = "medium"\n'
            'session_type = "chain"\n'
            'session_count = 1\n'
            '\n'
            '[[repos]]\n'
            'path = "repo-a"\n'
            '\n'
            '[[sessions]]\n'
            'prompt = "do the thing"\n'
            '\n'
            '[[checkpoints]]\n'
            'name = "cp"\n'
            'weight = 1.0\n'
            'verifier = "checks/broken.sh"\n'
        )
        checks = tmp_path / "checks"
        checks.mkdir()
        # Exits 0 and prints nothing: under the old code, a free 1.0.
        write_stub_verifier(checks, "#!/bin/sh\nexit 0\n", name="broken.sh")

        proc = subprocess.run(
            [sys.executable, "-m", "orchestration.chain_runner", str(task_toml), "--simulate"],
            capture_output=True,
            text=True,
            cwd=str(REPO_ROOT / "scripts"),
            timeout=120,
        )

        assert proc.returncode != 0, "an invalid run must not exit 0"

        payload = json.loads((tmp_path / "chain_result.json").read_text())
        assert payload["total_score"] is None, "no number for an invalid run"
        assert payload["verifier_infra_error"]["reason"] == NO_VERDICT_REASON
