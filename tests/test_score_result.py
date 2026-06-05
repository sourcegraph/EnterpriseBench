"""Tests for the unified ScoreResult contract emitted by eb_verify."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from eb_verify.scoring import (
    SCORER_FAMILY_CHECKLIST,
    CheckpointResult,
    ScoreDiagnostics,
    ScoreResult,
    VerificationResult,
    write_score_result,
)


def _vr(*, task_id: str = "t-001", total: float = 0.7) -> VerificationResult:
    return VerificationResult(
        task_id=task_id,
        checkpoint_results=[
            CheckpointResult(name="cp1", weight=0.5, passed=True, score=1.0),
            CheckpointResult(name="cp2", weight=0.5, passed=False, score=0.4),
        ],
        artifact_results=[
            {"type": "answer", "valid": True, "detail": "parsed"},
            {"type": "code_patch", "valid": False, "detail": "no patch"},
        ],
        total_score=total,
    )


class TestToScoreResult:
    def test_reward_matches_total_score(self):
        sr = _vr(total=0.7).to_score_result()
        assert sr.reward == pytest.approx(0.7)

    def test_scorer_family_is_checklist(self):
        sr = _vr().to_score_result()
        assert sr.scorer_family == SCORER_FAMILY_CHECKLIST == "checklist"

    def test_sub_scores_keyed_by_checkpoint_name(self):
        sr = _vr().to_score_result()
        assert sr.sub_scores == {"cp1": 1.0, "cp2": 0.4}

    def test_sub_scores_clamped_to_unit_interval(self):
        vr = VerificationResult(
            task_id="t",
            checkpoint_results=[
                CheckpointResult(name="hi", weight=1.0, passed=True, score=1.5),
                CheckpointResult(name="lo", weight=0.0, passed=False, score=-0.3),
            ],
            total_score=1.0,
        )
        sr = vr.to_score_result()
        assert sr.sub_scores == {"hi": 1.0, "lo": 0.0}

    def test_diagnostics_carries_harness_inputs(self):
        sr = _vr().to_score_result(
            task_time_seconds=12.5,
            token_cost_usd=0.0042,
            input_tokens=1289,
            output_tokens=35128,
        )
        assert sr.diagnostics.task_time_seconds == 12.5
        assert sr.diagnostics.token_cost_usd == pytest.approx(0.0042)
        assert sr.diagnostics.input_tokens == 1289
        assert sr.diagnostics.output_tokens == 35128

    def test_diagnostics_ir_metrics_is_none_for_eb(self):
        sr = _vr().to_score_result()
        assert sr.diagnostics.ir_metrics is None

    def test_artifact_results_keyed_by_type(self):
        sr = _vr().to_score_result()
        assert sr.diagnostics.artifact_results == {
            "answer": {"valid": True, "detail": "parsed"},
            "code_patch": {"valid": False, "detail": "no patch"},
        }

    def test_score_result_is_frozen(self):
        sr = _vr().to_score_result()
        with pytest.raises(Exception):  # FrozenInstanceError subclass of Exception
            sr.reward = 0.0  # type: ignore[misc]


class TestToDictWireFormat:
    def test_top_level_keys_match_contract(self):
        sr = _vr().to_score_result()
        wire = sr.to_dict()
        assert set(wire.keys()) == {
            "task_id",
            "reward",
            "scorer_family",
            "sub_scores",
            "diagnostics",
        }

    def test_diagnostics_keys_match_contract(self):
        sr = _vr().to_score_result()
        wire = sr.to_dict()
        assert set(wire["diagnostics"].keys()) == {
            "task_time_seconds",
            "token_cost_usd",
            "input_tokens",
            "output_tokens",
            "ir_metrics",
            "artifact_results",
        }

    def test_round_trips_through_json(self):
        sr = _vr().to_score_result(task_time_seconds=1.0, token_cost_usd=0.01)
        s = json.dumps(sr.to_dict())
        parsed = json.loads(s)
        assert parsed["reward"] == pytest.approx(0.7)
        assert parsed["scorer_family"] == "checklist"
        assert parsed["sub_scores"]["cp1"] == 1.0
        assert parsed["diagnostics"]["task_time_seconds"] == 1.0


class TestWriteScoreResult:
    def test_creates_json_file(self, tmp_path: Path):
        out = tmp_path / "score_result.json"
        path = write_score_result(_vr(), out)
        assert path == out
        assert out.exists()

    def test_file_is_valid_json(self, tmp_path: Path):
        out = tmp_path / "score_result.json"
        write_score_result(_vr(), out, task_time_seconds=2.0)
        data = json.loads(out.read_text())
        assert data["task_id"] == "t-001"
        assert data["diagnostics"]["task_time_seconds"] == 2.0

    def test_omitted_diagnostics_default_to_none(self, tmp_path: Path):
        out = tmp_path / "score_result.json"
        write_score_result(_vr(), out)
        data = json.loads(out.read_text())
        assert data["diagnostics"]["task_time_seconds"] is None
        assert data["diagnostics"]["token_cost_usd"] is None
        assert data["diagnostics"]["input_tokens"] is None
        assert data["diagnostics"]["output_tokens"] is None
        assert data["diagnostics"]["ir_metrics"] is None

    def test_raw_token_counts_are_emitted(self, tmp_path: Path):
        out = tmp_path / "score_result.json"
        write_score_result(_vr(), out, input_tokens=1289, output_tokens=35128)
        data = json.loads(out.read_text())
        assert data["diagnostics"]["input_tokens"] == 1289
        assert data["diagnostics"]["output_tokens"] == 35128


class TestScoreDiagnosticsDefaults:
    def test_defaults_are_none_and_empty(self):
        d = ScoreDiagnostics()
        assert d.task_time_seconds is None
        assert d.token_cost_usd is None
        assert d.input_tokens is None
        assert d.output_tokens is None
        assert d.ir_metrics is None
        assert d.artifact_results == {}

    def test_artifact_results_default_factory_is_independent(self):
        a = ScoreDiagnostics()
        b = ScoreDiagnostics()
        a.artifact_results["x"] = {"valid": True, "detail": ""}
        assert b.artifact_results == {}
