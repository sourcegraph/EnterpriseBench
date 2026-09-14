"""Tests for verifier-infrastructure failure classification in run_task.py.

A broken scoring harness (test.sh timeout, no output, unparseable JSON) must
surface as an infra failure — never as a silent, real-looking 0.0 score. This
closes the same fake-zero channel for verifiers that bead EnterpriseBench-s58f
closed for agent no-ops.

The marker these tests originally asserted, a ``verifier_failure`` key on the
scores dict, no longer exists: classification moved into
``eb_verify.scorer_guard``, which returns an ``InfraError`` that _run_scoring
stores under ``verifier_infra_error``. A verifier *timeout* is not caught here
at all -- it propagates to run_task's top-level handler, which records
failure_class ``infra_timeout`` and phase ``error``. Either way no score is
written, which is the invariant under test; these assert the contract that is
actually in force.
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

sys.path.insert(
    0, str(Path(__file__).resolve().parent.parent / "scripts" / "orchestration")
)
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts" / "infra"))

import run_task as rt


def _completed(stdout: str = "", stderr: str = "", returncode: int = 0):
    return SimpleNamespace(stdout=stdout, stderr=stderr, returncode=returncode)


class TestRunScoringFailureClassification:
    @pytest.fixture(autouse=True)
    def _preflight_holds(self, monkeypatch):
        """Neutralize the pre-scoring preflight so the blanket ``_docker_exec``
        stubs below only govern the verifier run itself. Each preflight step
        issues its own exec, and without this the first canned response is
        consumed by the seal check — short-circuiting every case into
        ``integrity_violation`` or a reap RuntimeError, both different channels
        than the verifier-output classification under test. The preflight has its
        own coverage in tests/integrity/test_grading_asset_seal.py.
        """
        monkeypatch.setattr(rt, "_assert_grading_assets_sealed", lambda cid: (True, ""))
        monkeypatch.setattr(rt, "_reap_agent_processes", lambda cid: None)
        monkeypatch.setattr(rt, "_close_workspace_for_scoring", lambda cid: None)

    def test_timeout_propagates_rather_than_scoring(self, monkeypatch) -> None:
        """A verifier that never returns must not resolve to a number here.

        run_task's top-level handler classifies the escaping TimeoutExpired as
        failure_class ``infra_timeout`` with phase ``error``. Swallowing it into
        a scores dict is what would turn a hung grader into a measured 0.0.
        """

        def _raise_timeout(*args, **kwargs):
            raise subprocess.TimeoutExpired(cmd="test.sh", timeout=600)

        monkeypatch.setattr(rt, "_docker_exec", _raise_timeout)

        with pytest.raises(subprocess.TimeoutExpired):
            rt._run_scoring("cid", verifier_timeout=600)

    def test_no_output_marks_verifier_infra_error(self, monkeypatch) -> None:
        monkeypatch.setattr(
            rt, "_docker_exec", lambda *a, **kw: _completed(stdout="", returncode=2)
        )
        scores = rt._run_scoring("cid")
        assert scores["verifier_infra_error"]
        assert scores["task_score"] == 0.0
        assert scores["all_passed"] is False

    def test_bad_json_marks_verifier_infra_error(self, monkeypatch) -> None:
        monkeypatch.setattr(
            rt,
            "_docker_exec",
            lambda *a, **kw: _completed(stdout="this is not json"),
        )
        scores = rt._run_scoring("cid")
        assert scores["verifier_infra_error"]
        assert scores["task_score"] == 0.0

    def test_valid_json_has_no_infra_marker(self, monkeypatch) -> None:
        # A non-empty checkpoint list is required: an empty one is itself an
        # infra signal (no verifier ran), not a real 0.5 measurement. Each
        # checkpoint must also carry the positive verifier_ran attestation
        # scorer_guard gates on -- absence of it, not any known-bad string, is
        # what routes a checkpoint to the infra channel.
        payload = (
            '{"task_score": 0.5, "all_passed": false,'
            ' "checkpoints": [{"name": "x", "score": 0.5, "passed": false,'
            ' "verifier_ran": true}]}'
        )
        monkeypatch.setattr(
            rt, "_docker_exec", lambda *a, **kw: _completed(stdout=payload)
        )
        scores = rt._run_scoring("cid")
        assert "verifier_infra_error" not in scores
        assert scores["task_score"] == 0.5

    def test_real_zero_score_is_not_a_failure(self, monkeypatch) -> None:
        # A genuine all-checkpoints-failed run is a measurement, not an error.
        payload = (
            '{"task_score": 0.0, "all_passed": false,'
            ' "checkpoints": [{"name": "x", "score": 0.0, "passed": false,'
            ' "verifier_ran": true}]}'
        )
        monkeypatch.setattr(
            rt, "_docker_exec", lambda *a, **kw: _completed(stdout=payload)
        )
        scores = rt._run_scoring("cid")
        assert "verifier_infra_error" not in scores
        assert scores["task_score"] == 0.0
