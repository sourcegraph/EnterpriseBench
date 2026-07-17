"""Tests for the results.json chain_runner writes (the reader-facing file).

chain_runner wrote a bare ``{"scores": {"task_score": ...}}`` before
EnterpriseBench-78zt5. That satisfied the one reader it was tested against
(run_benchmark's inline score read) and starved every other one: ``success`` was
absent, so skip-completed never saw the run and run_sweep could not classify it;
``tool_usage`` was absent, so the run contributed 0.0 to every cost rollup. An
absent key is indistinguishable from a real default at those read sites, so
nothing failed — the numbers were just quietly wrong.

These tests assert the shape chain_runner emits, using the real ChainResult /
ChainTaskDefinition dataclasses so a field rename breaks them rather than
sliding past a hand-rolled double.

Shape alone turned out to be too weak a claim: the payload can carry every key a
reader wants and still tell it a simulation was a real agent trial. So the
validity tests below assert what the run means, once through the adapter that
actually consumes the payload.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path
from unittest.mock import patch

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT / "scripts"))
sys.path.insert(0, str(REPO_ROOT / "scripts" / "orchestration"))

from orchestration.chain_runner import (  # noqa: E402
    ChainResult,
    ChainTaskDefinition,
    _no_agent_configured,
    _session_failure,
    main,
)
from eb_metrics.trace_quality_adapter import metrics_from_eb_record  # noqa: E402
from eb_verify.scorer_guard import InfraError  # noqa: E402
from results_io import RESULTS_FIELDS, RESULTS_FILENAME, RUN_STATUS_INVALID  # noqa: E402


def _task_def() -> ChainTaskDefinition:
    return ChainTaskDefinition(
        task_id="chain-test-001",
        suite="feature_delivery",
        difficulty="medium",
        session_count=2,
        repos=[],
        sessions=[],
        final_checkpoints=[],
        metadata={"languages": ["python"]},
    )


def _run_main(
    tmp_path: Path, result: ChainResult, *argv: str
) -> tuple[int, dict]:
    """Run chain_runner.main against *result* and return (exit_code, results.json)."""
    task_toml = tmp_path / "task.toml"
    task_toml.write_text("# fixture\n")

    with patch("orchestration.chain_runner.parse_chain_task", return_value=_task_def()), \
         patch("orchestration.chain_runner.run_chain", return_value=result), \
         patch("sys.argv", ["chain_runner", str(task_toml), "--mode", "baseline", *argv]):
        code = main()

    payload_path = tmp_path / RESULTS_FILENAME
    payload = json.loads(payload_path.read_text()) if payload_path.exists() else {}
    return code, payload


def _scored_result() -> ChainResult:
    return ChainResult(task_id="chain-test-001", total_sessions=2, total_score=0.75)


class TestScoredChain:
    def test_results_json_is_written(self, tmp_path: Path) -> None:
        code, payload = _run_main(tmp_path, _scored_result())
        assert code == 0
        assert payload != {}

    def test_carries_the_shared_shape(self, tmp_path: Path) -> None:
        """Same contract run_task and event_replay write — not a chain dialect."""
        _, payload = _run_main(tmp_path, _scored_result())
        assert tuple(payload) == RESULTS_FIELDS

    def test_score_still_reaches_the_score_reader(self, tmp_path: Path) -> None:
        """The one reader the old bare payload did satisfy must not regress."""
        _, payload = _run_main(tmp_path, _scored_result())
        assert payload["scores"]["task_score"] == pytest.approx(0.75)

    def test_success_is_present_for_skip_completed_and_sweep(self, tmp_path: Path) -> None:
        """Regression: absent `success` made a real run invisible to both readers."""
        _, payload = _run_main(tmp_path, _scored_result())
        assert payload["success"] is True

    def test_tool_usage_is_present_for_the_cost_rollup(self, tmp_path: Path) -> None:
        """Regression: absent `tool_usage` silently contributed 0.0 to every total.

        Chains have no per-agent cost accounting yet, so the value is empty —
        but the key exists, so the rollup reads a real absence, not a hole.
        """
        _, payload = _run_main(tmp_path, _scored_result())
        assert "tool_usage" in payload
        assert payload["tool_usage"] == {}

    def test_phase_complete_keeps_it_in_the_headline(self, tmp_path: Path) -> None:
        _, payload = _run_main(tmp_path, _scored_result())
        assert payload["phase"] == "complete"

    def test_task_metadata_comes_from_the_task_definition(self, tmp_path: Path) -> None:
        _, payload = _run_main(tmp_path, _scored_result())
        assert payload["task_id"] == "chain-test-001"
        assert payload["task_metadata"]["suite"] == "feature_delivery"
        assert payload["task_metadata"]["difficulty"] == "medium"
        assert payload["task_metadata"]["languages"] == ["python"]


class TestUnscoredChain:
    """A run that produced no score must not read as a successful one."""

    def test_clean_chain_with_no_score_is_not_success(self, tmp_path: Path) -> None:
        """No milestones scored: belongs in run_sweep's `failed`, not the table."""
        code, payload = _run_main(
            tmp_path, ChainResult(task_id="chain-test-001", total_score=None)
        )
        assert code == 0
        assert payload["success"] is False
        assert payload["phase"] == ""
        assert payload["scores"]["task_score"] is None

    @pytest.mark.parametrize(
        "channel, cause",
        [
            (
                "verifier_infra_error",
                InfraError(
                    reason="verifier_infra_error",
                    stage="verify",
                    detail="verifier never reached a verdict",
                ).as_verifier_error(),
            ),
            ("session_failure", _session_failure(1, "session never completed")),
            ("agent_not_configured", _no_agent_configured()),
        ],
    )
    def test_every_invalidity_channel_is_marked_invalid_and_exits_nonzero(
        self, tmp_path: Path, channel: str, cause: dict
    ) -> None:
        """status=invalid is what the aggregator drops on; nonzero routes it to
        run_benchmark's error channel so it is never recorded as completed.

        The causes come from the real factories, so a change to the payload shape
        breaks this test rather than sliding past a hand-rolled dict.
        """
        code, payload = _run_main(
            tmp_path,
            ChainResult(task_id="chain-test-001", total_score=None, **{channel: cause}),
        )
        assert code == 1
        assert payload["success"] is False
        assert payload["status"] == RUN_STATUS_INVALID
        # A reader must never be handed a number from a run that did not score.
        assert payload["scores"]["task_score"] is None

    @pytest.mark.parametrize(
        "channel, cause",
        [
            (
                "verifier_infra_error",
                InfraError(
                    reason="verifier_infra_error",
                    stage="verify",
                    detail="verifier never reached a verdict",
                ).as_verifier_error(),
            ),
            ("session_failure", _session_failure(1, "session never completed")),
            ("agent_not_configured", _no_agent_configured()),
        ],
    )
    def test_every_invalidity_channel_reports_its_own_cause(
        self, tmp_path: Path, channel: str, cause: dict
    ) -> None:
        """Which cause, not just that there was one.

        Every invalid chain landed failure_class=null / error="" before this, so
        the three causes ChainResult already distinguishes collapsed into one
        bucket and triage read validity_reason="unknown" for all of them — and
        their remedies differ: a verifier fault earns a re-run, a missing agent
        needs a harness. run_task pairs every status=invalid with a
        failure_class; chain_runner was the only writer that did not.
        """
        _, payload = _run_main(
            tmp_path,
            ChainResult(task_id="chain-test-001", total_score=None, **{channel: cause}),
        )
        assert payload["failure_class"] == channel
        assert payload["error"] == cause["detail"]
        assert payload["error"] != ""

    def test_the_first_channel_is_reported_when_several_fired(
        self, tmp_path: Path
    ) -> None:
        """A chain can fail more than one way at once; the payload has one
        failure_class, so it names the verifier fault it hit first. The rest stay
        in chain_result.json, which carries every channel that fired."""
        _, payload = _run_main(
            tmp_path,
            ChainResult(
                task_id="chain-test-001",
                total_score=None,
                verifier_infra_error=InfraError(
                    reason="verifier_infra_error", stage="verify", detail="no verdict"
                ).as_verifier_error(),
                session_failure=_session_failure(1, "session never completed"),
            ),
        )
        assert payload["failure_class"] == "verifier_infra_error"


class TestSimulatedChain:
    """A simulation scores marker files the scaffold wrote, not agent work.

    The runner recorded `simulated` under `config` only, which no validity reader
    consults — so a simulated chain wrote success=true / phase=complete and a real
    task_score, and projected as a genuine agent trial. That was strictly worse
    than the bare payload it replaced: the old one at least failed loudly at the
    adapter and could not enter the scored population at all.
    """

    def _simulated(self) -> ChainResult:
        return ChainResult(task_id="chain-test-001", total_score=0.87, simulated=True)

    def test_simulation_is_not_a_successful_run(self, tmp_path: Path) -> None:
        _, payload = _run_main(tmp_path, self._simulated(), "--simulate")
        assert payload["success"] is False
        assert payload["phase"] == ""
        assert payload["status"] == RUN_STATUS_INVALID

    def test_simulation_names_itself_where_a_reader_looks(self, tmp_path: Path) -> None:
        """failure_class is the channel every validity reader already reads. A new
        top-level field would only reach readers that opt in to consulting it —
        which is this bead's own bug restated."""
        _, payload = _run_main(tmp_path, self._simulated(), "--simulate")
        assert payload["failure_class"] == "simulated"
        assert "simulate" in payload["error"]

    def test_simulation_exits_zero(self, tmp_path: Path) -> None:
        """The scaffold ran exactly as asked. Its run is unscoreable, not failed —
        and run_benchmark never forwards --simulate, so nothing dispatches here."""
        code, _ = _run_main(tmp_path, self._simulated(), "--simulate")
        assert code == 0

    def test_the_scaffold_score_is_still_reported(self, tmp_path: Path) -> None:
        """It is a real number about the scaffold, kept for whoever ran the
        simulation on purpose. The validity fields, not a missing score, are what
        keep it out of the results table."""
        _, payload = _run_main(tmp_path, self._simulated(), "--simulate")
        assert payload["scores"]["task_score"] == pytest.approx(0.87)

    def test_simulation_cannot_enter_the_scored_population(self, tmp_path: Path) -> None:
        """The end-to-end claim, asserted through the real consumer: shape tests
        passed while this projected as validity="valid" score=0.87."""
        _, payload = _run_main(tmp_path, self._simulated(), "--simulate")
        metrics = metrics_from_eb_record(payload, run_name="test-run")
        assert metrics.validity == "invalid"
        assert metrics.score is None
        assert metrics.validity_reason == "simulated"


class TestStaleResultsAreCleared:
    def test_previous_results_are_unlinked_before_the_run(self, tmp_path: Path) -> None:
        """A raise mid-run must not leave the previous run's score as this one's."""
        stale = tmp_path / RESULTS_FILENAME
        stale.write_text(json.dumps({"scores": {"task_score": 9.9}}))

        task_toml = tmp_path / "task.toml"
        task_toml.write_text("# fixture\n")

        with patch("orchestration.chain_runner.parse_chain_task", return_value=_task_def()), \
             patch("orchestration.chain_runner.run_chain", side_effect=RuntimeError("boom")), \
             patch("sys.argv", ["chain_runner", str(task_toml), "--mode", "baseline"]):
            with pytest.raises(RuntimeError):
                main()

        assert not stale.exists()
