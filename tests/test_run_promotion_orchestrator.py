"""Tests for the RunPromotionOrchestrator.

The tests stub the read-only validators and the score/chart/report
generators so the orchestrator can be exercised without real benchmark
data. The orchestrator under test is a sequencer with rollback hooks —
the unit tests cover the contract (atomicity, resume, validate-only,
forensics) rather than the upstream tools' behaviour.
"""

from __future__ import annotations

import json
import sys
from dataclasses import replace
from pathlib import Path
from typing import Any

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT / "scripts" / "orchestration"))

import run_promotion_orchestrator as rpo  # noqa: E402

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def workdir(tmp_path: Path) -> Path:
    """Create a fake repo layout inside a temporary directory."""
    raw_runs = tmp_path / "results" / "runs"
    raw_runs.mkdir(parents=True)
    official_runs = tmp_path / "results" / "official_runs"
    official_runs.mkdir(parents=True)
    return tmp_path


def _make_run(workdir: Path, run_id: str) -> Path:
    raw_run_dir = workdir / "results" / "runs" / run_id
    raw_run_dir.mkdir(parents=True)
    (raw_run_dir / "results.json").write_text(json.dumps({"task_id": "t1"}))
    return raw_run_dir


def _make_ctx(workdir: Path, run_id: str, **overrides: Any) -> rpo.PromotionContext:
    base = rpo.build_context(
        run_id=run_id,
        target_state="official",
        repo_root=workdir,
        raw_runs_root=workdir / "results" / "runs",
        official_runs_root=workdir / "results" / "official_runs",
    )
    if overrides:
        base = replace(base, **overrides)
    return base


def _step(name: str, fail: bool = False) -> rpo.Step:
    """Build a synthetic step that creates a marker file or raises."""
    marker_state: dict[str, Path] = {}

    def execute(ctx: rpo.PromotionContext) -> rpo.StepOutcome:
        if fail:
            raise RuntimeError(f"step {name} failed by design")
        ctx.staging_dir.mkdir(parents=True, exist_ok=True)
        marker = ctx.staging_dir / f"{name}.marker"
        marker.write_text(name)
        marker_state[name] = marker
        return rpo.StepOutcome(
            step_name=name, status="reversible", details=f"created {marker.name}"
        )

    def rollback(_ctx: rpo.PromotionContext) -> None:
        marker = marker_state.get(name)
        if marker and marker.is_file():
            marker.unlink()

    return rpo.Step(name=name, execute=execute, rollback=rollback)


# ---------------------------------------------------------------------------
# Context validation
# ---------------------------------------------------------------------------


class TestContext:
    def test_invalid_target_state_raises(self, workdir: Path) -> None:
        ctx = _make_ctx(workdir, "r1", target_state="bogus")
        with pytest.raises(ValueError, match="Invalid target_state"):
            rpo.RunPromotionOrchestrator(ctx)

    def test_negative_resume_from_raises(self, workdir: Path) -> None:
        ctx = _make_ctx(workdir, "r1", resume_from=-1)
        with pytest.raises(ValueError, match="resume_from must be >= 0"):
            rpo.RunPromotionOrchestrator(ctx)

    def test_default_pipeline_has_nine_steps(self) -> None:
        pipeline = rpo.build_default_pipeline()
        names = [s.name for s in pipeline]
        assert names == [
            "validate_inputs",
            "validate_tasks_preflight",
            "validate_crnt",
            "validate_expected_solutions",
            "stage_metrics",
            "stage_charts",
            "stage_report",
            "atomic_publish",
            "update_registry",
        ]


# ---------------------------------------------------------------------------
# Validate-only mode
# ---------------------------------------------------------------------------


class TestValidateOnly:
    def test_validate_only_runs_only_validators(self, workdir: Path) -> None:
        _make_run(workdir, "r1")
        ctx = _make_ctx(workdir, "r1")
        validators = [
            rpo.Step(
                "validate_a", lambda c: rpo.StepOutcome("validate_a", "reversible")
            ),
            rpo.Step(
                "validate_b", lambda c: rpo.StepOutcome("validate_b", "reversible")
            ),
            _step("stage_x", fail=False),
        ]
        report = rpo.RunPromotionOrchestrator(ctx, validators).run(validate_only=True)
        assert report.succeeded
        assert [s.step_name for s in report.steps] == ["validate_a", "validate_b"]
        assert not ctx.staging_dir.exists()


# ---------------------------------------------------------------------------
# Atomic publish
# ---------------------------------------------------------------------------


class TestAtomicPublish:
    def test_publish_renames_staging_to_final(self, workdir: Path) -> None:
        _make_run(workdir, "r1")
        ctx = _make_ctx(workdir, "r1")
        ctx.staging_dir.mkdir(parents=True)
        (ctx.staging_dir / "report.md").write_text("# done")

        outcome = rpo._step_atomic_publish(ctx)
        assert outcome.status == "reversible"
        assert ctx.final_dir.is_dir()
        assert (ctx.final_dir / "report.md").read_text() == "# done"
        assert not ctx.staging_dir.exists()

    def test_publish_refuses_existing_final(self, workdir: Path) -> None:
        _make_run(workdir, "r1")
        ctx = _make_ctx(workdir, "r1")
        ctx.staging_dir.mkdir(parents=True)
        ctx.final_dir.mkdir(parents=True)

        with pytest.raises(RuntimeError, match="final dir already exists"):
            rpo._step_atomic_publish(ctx)

    def test_publish_dry_run_does_not_move(self, workdir: Path) -> None:
        _make_run(workdir, "r1")
        ctx = _make_ctx(workdir, "r1", dry_run=True)
        ctx.staging_dir.mkdir(parents=True)

        outcome = rpo._step_atomic_publish(ctx)
        assert outcome.status == "dry_run"
        assert ctx.staging_dir.exists()
        assert not ctx.final_dir.exists()


# ---------------------------------------------------------------------------
# Rollback / atomicity
# ---------------------------------------------------------------------------


class TestRollback:
    def test_failure_rolls_back_completed_steps_lifo(self, workdir: Path) -> None:
        rollback_order: list[str] = []

        def make_step(name: str, fail: bool = False) -> rpo.Step:
            def execute(ctx: rpo.PromotionContext) -> rpo.StepOutcome:
                if fail:
                    raise RuntimeError(f"{name} failed")
                ctx.staging_dir.mkdir(parents=True, exist_ok=True)
                (ctx.staging_dir / f"{name}.marker").write_text(name)
                return rpo.StepOutcome(name, "reversible")

            def rollback(_ctx: rpo.PromotionContext) -> None:
                rollback_order.append(name)

            return rpo.Step(name=name, execute=execute, rollback=rollback)

        ctx = _make_ctx(workdir, "r1")
        pipeline = [
            make_step("a"),
            make_step("b"),
            make_step("c", fail=True),
        ]
        report = rpo.RunPromotionOrchestrator(ctx, pipeline).run()
        assert not report.succeeded
        # Failed step (c) never produced a reversible outcome — only a/b roll back.
        assert rollback_order == ["b", "a"]

    def test_staging_rollback_removes_directory(self, workdir: Path) -> None:
        ctx = _make_ctx(workdir, "r1")
        ctx.staging_dir.mkdir(parents=True)
        (ctx.staging_dir / "x").write_text("x")
        rpo._rollback_staging(ctx)
        assert not ctx.staging_dir.exists()

    def test_publish_rollback_returns_dir_to_staging(self, workdir: Path) -> None:
        ctx = _make_ctx(workdir, "r1")
        ctx.final_dir.mkdir(parents=True)
        (ctx.final_dir / "x").write_text("x")
        rpo._rollback_atomic_publish(ctx)
        assert ctx.staging_dir.is_dir()
        assert (ctx.staging_dir / "x").read_text() == "x"
        assert not ctx.final_dir.exists()

    def test_registry_rollback_restores_backup(self, workdir: Path) -> None:
        ctx = _make_ctx(workdir, "r1")
        ctx.registry_path.parent.mkdir(parents=True, exist_ok=True)
        ctx.registry_path.write_text(json.dumps({"entries": ["new"]}))
        backup = ctx.registry_path.with_suffix(ctx.registry_path.suffix + ".bak")
        backup.write_text(json.dumps({"entries": ["old"]}))

        rpo._rollback_registry(ctx)
        restored = json.loads(ctx.registry_path.read_text())
        assert restored == {"entries": ["old"]}


# ---------------------------------------------------------------------------
# Resume
# ---------------------------------------------------------------------------


class TestResume:
    def test_resume_from_step_skips_earlier_steps(self, workdir: Path) -> None:
        executed: list[str] = []

        def step(name: str) -> rpo.Step:
            def execute(_ctx: rpo.PromotionContext) -> rpo.StepOutcome:
                executed.append(name)
                return rpo.StepOutcome(name, "reversible")

            return rpo.Step(name=name, execute=execute)

        ctx = _make_ctx(workdir, "r1", resume_from=3)
        pipeline = [step("a"), step("b"), step("c"), step("d")]
        report = rpo.RunPromotionOrchestrator(ctx, pipeline).run()
        assert report.succeeded
        # Only steps 3 and 4 actually executed.
        assert executed == ["c", "d"]
        # Skipped steps appear in the report with status "skipped".
        statuses = {s.step_name: s.status for s in report.steps}
        assert statuses["a"] == "skipped"
        assert statuses["b"] == "skipped"
        assert statuses["c"] == "reversible"
        assert statuses["d"] == "reversible"


# ---------------------------------------------------------------------------
# Validate inputs
# ---------------------------------------------------------------------------


class TestValidateInputs:
    def test_missing_run_dir_raises(self, workdir: Path) -> None:
        ctx = _make_ctx(workdir, "missing")
        with pytest.raises(FileNotFoundError):
            rpo._step_validate_inputs(ctx)

    def test_empty_run_dir_raises(self, workdir: Path) -> None:
        ctx = _make_ctx(workdir, "empty")
        ctx.raw_run_dir.mkdir(parents=True)
        with pytest.raises(ValueError, match="empty"):
            rpo._step_validate_inputs(ctx)

    def test_populated_run_dir_passes(self, workdir: Path) -> None:
        _make_run(workdir, "good")
        ctx = _make_ctx(workdir, "good")
        outcome = rpo._step_validate_inputs(ctx)
        assert outcome.status == "reversible"


# ---------------------------------------------------------------------------
# Stage metrics
# ---------------------------------------------------------------------------


def _fake_analyzer(
    monkeypatch: pytest.MonkeyPatch,
    calls: list[list[str]],
    total_results: int = 3,
    payload: str | None = None,
) -> None:
    """Stub ``_run_subprocess`` with an analyzer that records its argv."""

    def fake(cmd: list[str], _cwd: Path) -> tuple[int, str, str]:
        calls.append(cmd)
        output = Path(cmd[cmd.index("--output") + 1])
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(
            payload
            if payload is not None
            else json.dumps({"total_results": total_results})
        )
        return 0, "", ""

    monkeypatch.setattr(rpo, "_run_subprocess", fake)


class TestStageMetrics:
    def test_analyzer_is_scoped_to_the_validated_run(
        self, workdir: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        _make_run(workdir, "r1")
        # A second, unrelated run must not be visible to the analyzer.
        _make_run(workdir, "quarantined")
        ctx = _make_ctx(workdir, "r1")
        calls: list[list[str]] = []
        _fake_analyzer(monkeypatch, calls)

        outcome = rpo._step_stage_metrics(ctx)

        assert outcome.status == "reversible"
        assert len(calls) == 1
        cmd = calls[0]
        assert cmd[cmd.index("--results-dir") + 1] == str(ctx.raw_run_dir)

    def test_empty_analysis_is_rejected(
        self, workdir: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        _make_run(workdir, "r1")
        ctx = _make_ctx(workdir, "r1")
        _fake_analyzer(monkeypatch, [], total_results=0)

        with pytest.raises(RuntimeError, match="no results"):
            rpo._step_stage_metrics(ctx)

    def test_unreadable_analysis_is_rejected(
        self, workdir: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        _make_run(workdir, "r1")
        ctx = _make_ctx(workdir, "r1")
        _fake_analyzer(monkeypatch, [], payload="not json")

        with pytest.raises(RuntimeError, match="unreadable"):
            rpo._step_stage_metrics(ctx)


# ---------------------------------------------------------------------------
# Registry update
# ---------------------------------------------------------------------------


class TestRegistry:
    def test_first_promotion_creates_registry(self, workdir: Path) -> None:
        ctx = _make_ctx(workdir, "r1")
        ctx.final_dir.mkdir(parents=True)

        outcome = rpo._step_update_registry(ctx)
        assert outcome.status == "reversible"
        registry = json.loads(ctx.registry_path.read_text())
        assert len(registry["entries"]) == 1
        assert registry["entries"][0]["run_id"] == "r1"

    def test_reentry_replaces_existing(self, workdir: Path) -> None:
        ctx = _make_ctx(workdir, "r1")
        ctx.final_dir.mkdir(parents=True)
        ctx.registry_path.parent.mkdir(parents=True, exist_ok=True)
        ctx.registry_path.write_text(
            json.dumps(
                {
                    "entries": [
                        {"run_id": "r1", "target_state": "candidate"},
                        {"run_id": "r2", "target_state": "official"},
                    ]
                }
            )
        )

        rpo._step_update_registry(ctx)
        registry = json.loads(ctx.registry_path.read_text())
        run_ids = [e["run_id"] for e in registry["entries"]]
        # r2 preserved, r1 replaced (single entry per run_id).
        assert run_ids == ["r2", "r1"]
        assert registry["entries"][1]["target_state"] == "official"

    def test_dry_run_does_not_write(self, workdir: Path) -> None:
        ctx = _make_ctx(workdir, "r1", dry_run=True)
        outcome = rpo._step_update_registry(ctx)
        assert outcome.status == "dry_run"
        assert not ctx.registry_path.exists()


# ---------------------------------------------------------------------------
# Forensics
# ---------------------------------------------------------------------------


class TestForensics:
    def test_failure_writes_forensic_snapshot(self, workdir: Path) -> None:
        ctx = _make_ctx(workdir, "r1")
        pipeline = [
            _step("a"),
            _step("crash", fail=True),
        ]
        report = rpo.RunPromotionOrchestrator(ctx, pipeline).run()
        assert not report.succeeded
        assert report.forensics_path is not None
        forensics = Path(report.forensics_path)
        assert forensics.is_dir()
        assert (forensics / "context.json").is_file()
        assert (forensics / "error.json").is_file()
        assert (forensics / "completed_steps.json").is_file()

        err = json.loads((forensics / "error.json").read_text())
        assert err["type"] == "RuntimeError"
        assert "crash failed" in err["message"]


# ---------------------------------------------------------------------------
# CLI smoke test
# ---------------------------------------------------------------------------


class TestCli:
    def test_cli_reports_failure_on_missing_run(
        self, workdir: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        # Force the orchestrator to use the workdir-rooted layout.
        original_repo_root = rpo.REPO_ROOT
        rpo.REPO_ROOT = workdir
        try:
            rc = rpo.main(
                [
                    "--run-id",
                    "does-not-exist",
                    "--validate-only",
                    "--json",
                ]
            )
        finally:
            rpo.REPO_ROOT = original_repo_root

        assert rc == 1
        captured = capsys.readouterr().out
        report = json.loads(captured)
        assert report["succeeded"] is False
        assert report["run_id"] == "does-not-exist"
