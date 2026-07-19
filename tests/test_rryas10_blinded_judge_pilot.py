"""Invariant tests for the blinded PR-review-like judging pilot (rryas.10).

The pilot (``scripts/analysis/rryas10_blinded_judge_pilot.py``) is only worth
reporting if three properties hold, so they are pinned here rather than trusted:

1. **Blinding fails closed.** A payload that names an arm is refused, not judged.
2. **An infra error never becomes a score.** A backend/parse failure is recorded
   as an infra error and dropped from the agreement stats, honoring the
   ``scorer_guard`` rule that a missing score never turns into a real one.
3. **No blending.** The judge score stays a parallel column; it is never mixed
   into (or min()'d against) the deterministic reward.

All three run offline: ``LLMJudge`` is stubbed, so no judge backend is called.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT / "scripts" / "analysis"))

import rryas10_blinded_judge_pilot as pilot  # noqa: E402

from eb_verify.judge.backends import JudgeBackendError  # noqa: E402
from eb_verify.judge.models import (  # noqa: E402
    CheckpointJudgeResult,
    JudgeScoreError,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


class _StubJudge:
    """Stand-in for LLMJudge: returns canned scores, or raises per checkpoint.

    ``raises`` maps checkpoint name -> exception instance to raise instead of
    returning a verdict, which is how an infra failure is simulated.
    """

    def __init__(self, scores: dict, raises: dict | None = None) -> None:
        self.scores = scores
        self.raises = raises or {}
        self.seen: list = []

    def evaluate_checkpoint(self, judge_input, task_description, checkpoint_description):
        self.seen.append(judge_input)
        name = judge_input.checkpoint_name
        if name in self.raises:
            raise self.raises[name]
        score = self.scores[name]
        return CheckpointJudgeResult(
            checkpoint_name=name,
            score=score,
            passed=score >= 0.5,
            reasoning=f"stub reasoning for {name}",
            evidence=f"stub evidence for {name}",
            confidence="high",
        )


def _make_unit(
    tmp_path: Path,
    *,
    task_id: str = "task-001",
    arm: str = "baseline",
    answer: str = '{"summary": "an arm-neutral answer"}',
    checkpoints: dict | None = None,
    det_scores: dict | None = None,
    det_total: float | None = 1.0,
) -> pilot.Unit:
    """Build a Unit backed by real on-disk answer + expected_solution files."""
    checkpoints = checkpoints or {"cp_a": {}}
    det_scores = det_scores if det_scores is not None else dict.fromkeys(checkpoints, 1.0)

    unit_dir = tmp_path / f"{task_id}-{arm}"
    unit_dir.mkdir(parents=True)
    answer_path = unit_dir / "answer.json"
    answer_path.write_text(answer)

    task_dir = unit_dir / "benchmark"
    task_dir.mkdir()
    (task_dir / "expected_solution.json").write_text(
        json.dumps(
            {
                "checkpoints": {
                    name: {
                        "expected_solution": f"expected for {name}",
                        "evaluation_criteria": [f"must mention {name}"],
                    }
                    for name in checkpoints
                }
            }
        )
    )
    return pilot.Unit(
        task_id=task_id,
        arm=arm,
        answer_path=str(answer_path),
        task_dir=str(task_dir),
        deterministic_total=det_total,
        deterministic_checkpoints={
            name: {"score": det_scores.get(name), "weight": 1.0} for name in checkpoints
        },
    )


# ---------------------------------------------------------------------------
# 1. Blinding fails closed
# ---------------------------------------------------------------------------


class TestArmBlindingFailsClosed:
    @pytest.mark.parametrize(
        "payload",
        [
            '{"mode": "mcp_only", "summary": "found it"}',
            '{"summary": "ran with mode=hybrid"}',
            '{"MODE": "Baseline"}',  # case must not launder the tell
        ],
    )
    def test_arm_tell_refuses_to_judge(self, payload: str) -> None:
        with pytest.raises(ValueError, match="refusing to judge"):
            pilot._assert_arm_neutral(payload)

    def test_arm_neutral_payload_passes(self) -> None:
        pilot._assert_arm_neutral('{"summary": "no arm named here"}')

    def test_build_blinded_inputs_rejects_leaky_answer(self, tmp_path: Path) -> None:
        """The assertion is wired into the path that builds judge input."""
        unit = _make_unit(tmp_path, answer='{"mode": "mcp_only"}')
        with pytest.raises(ValueError, match="refusing to judge"):
            pilot.build_blinded_inputs(unit)

    def test_judge_input_carries_no_arm(self, tmp_path: Path) -> None:
        """Arm is held on the Unit for post-hoc analysis, never on the input."""
        unit = _make_unit(tmp_path, arm="mcp_only")
        for _, judge_input in pilot.build_blinded_inputs(unit):
            assert "mcp_only" not in json.dumps(judge_input.__dict__)


# ---------------------------------------------------------------------------
# 2. An infra error never becomes a score
# ---------------------------------------------------------------------------


class TestInfraErrorIsNotAScore:
    @pytest.mark.parametrize(
        "exc",
        [
            JudgeBackendError("backend unreachable"),
            JudgeScoreError("judge returned a non-score: 'unknown'"),
        ],
    )
    def test_failure_is_recorded_not_scored(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, exc: Exception
    ) -> None:
        unit = _make_unit(
            tmp_path,
            checkpoints={"cp_ok": {}, "cp_broken": {}},
            det_scores={"cp_ok": 1.0, "cp_broken": 1.0},
        )
        stub = _StubJudge({"cp_ok": 1.0}, raises={"cp_broken": exc})
        monkeypatch.setattr(pilot, "LLMJudge", lambda **kw: stub)

        out = tmp_path / "out"
        report = pilot.run_pilot([unit], model="stub", seed=1, out_dir=out)

        assert report["sample"]["infra_errors"] == 1
        assert report["sample"]["checkpoints_judged"] == 1
        detail = report["infra_errors_detail"][0]
        assert detail["checkpoint"] == "cp_broken"
        assert "score" not in detail

        # The failed checkpoint contributes no verdict at all — in particular
        # not a 0.0 that would read as "the judge disagreed".
        rows = [
            json.loads(line)
            for line in (out / "verdicts.jsonl").read_text().splitlines()
        ]
        assert [r["checkpoint"] for r in rows] == ["cp_ok"]

    def test_infra_error_excluded_from_agreement_stats(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """A failed checkpoint must not be banked as perfect disagreement.

        Deterministic says 1.0 on both checkpoints and the judge agrees on the
        one it reached. Coercing the failure to 0.0 would drag pass-agreement to
        0.5 and mean abs-diff to 0.5; excluding it leaves clean agreement.
        """
        unit = _make_unit(
            tmp_path,
            checkpoints={"cp_ok": {}, "cp_broken": {}},
            det_scores={"cp_ok": 1.0, "cp_broken": 1.0},
        )
        stub = _StubJudge(
            {"cp_ok": 1.0}, raises={"cp_broken": JudgeBackendError("boom")}
        )
        monkeypatch.setattr(pilot, "LLMJudge", lambda **kw: stub)

        report = pilot.run_pilot([unit], model="stub", seed=1, out_dir=tmp_path / "out")

        assert report["sample"]["checkpoints_paired_with_deterministic"] == 1
        assert report["checkpoint_agreement"]["pass_agreement_rate"] == 1.0
        assert report["checkpoint_agreement"]["abs_diff"] == {
            "n": 1,
            "mean": 0.0,
            "median": 0.0,
            "max": 0.0,
        }


# ---------------------------------------------------------------------------
# 3. No blending into the deterministic reward
# ---------------------------------------------------------------------------


class TestNoBlending:
    def test_judge_and_deterministic_stay_separate_columns(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Judge 0.0 against deterministic 1.0: the 1.0 must survive intact.

        Any blend — min(), mean(), or overwrite — would show up here as a
        deterministic_total that moved.
        """
        unit = _make_unit(
            tmp_path,
            checkpoints={"cp_a": {}},
            det_scores={"cp_a": 1.0},
            det_total=1.0,
        )
        monkeypatch.setattr(pilot, "LLMJudge", lambda **kw: _StubJudge({"cp_a": 0.0}))

        report = pilot.run_pilot([unit], model="stub", seed=1, out_dir=tmp_path / "out")

        row = report["units"][0]
        assert row["deterministic_total"] == 1.0
        assert row["judge_total"] == 0.0
        assert row["delta"] == -1.0
        assert report["config"]["blending"].startswith("none")

    def test_deterministic_artifacts_are_not_rewritten(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """The pilot is read-only w.r.t. the run it judges: only out_dir changes."""
        unit = _make_unit(tmp_path, checkpoints={"cp_a": {}})
        answer_before = Path(unit.answer_path).read_bytes()
        expected_path = Path(unit.task_dir) / "expected_solution.json"
        expected_before = expected_path.read_bytes()
        monkeypatch.setattr(pilot, "LLMJudge", lambda **kw: _StubJudge({"cp_a": 0.5}))

        out = tmp_path / "out"
        pilot.run_pilot([unit], model="stub", seed=1, out_dir=out)

        assert Path(unit.answer_path).read_bytes() == answer_before
        assert expected_path.read_bytes() == expected_before
        assert sorted(p.name for p in out.iterdir()) == ["report.json", "verdicts.jsonl"]

    def test_ledger_keeps_the_rationale_production_discards(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """runner.py returns only result.score; the pilot's value is the rest."""
        unit = _make_unit(tmp_path, arm="mcp_only", checkpoints={"cp_a": {}})
        monkeypatch.setattr(pilot, "LLMJudge", lambda **kw: _StubJudge({"cp_a": 0.5}))

        out = tmp_path / "out"
        pilot.run_pilot([unit], model="stub", seed=1, out_dir=out)

        row = json.loads((out / "verdicts.jsonl").read_text().strip())
        assert row["reasoning"] == "stub reasoning for cp_a"
        assert row["evidence"] == "stub evidence for cp_a"
        assert row["confidence"] == "high"
        # Arm appears in the post-hoc ledger only — it never reached the judge.
        assert row["arm"] == "mcp_only"
