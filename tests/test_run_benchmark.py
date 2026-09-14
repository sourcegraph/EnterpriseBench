"""Tests for scripts/run_benchmark.py — skip-completed and token watchdog."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from unittest.mock import patch

# Import the module under test
import sys
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))

import run_benchmark
from run_benchmark import (
    TaskInfo,
    TaskResult,
    TokenBudget,
    is_task_completed,
    filter_completed_tasks,
    build_parser,
    filter_tasks,
    manifest_task_ids,
    write_summary,
    main,
    PROJECT_ROOT,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_task_info(task_id: str = "test-001", difficulty: str = "medium") -> TaskInfo:
    return TaskInfo(
        task_id=task_id,
        suite="customer_escalation",
        difficulty=difficulty,
        session_type="single",
        task_type="error_provenance",
        toml_path=Path("/fake/task.toml"),
    )


def _write_results(results_dir: Path, task_id: str, success: bool, *, mode: str | None = None) -> Path:
    """Write a results.json file in the expected location."""
    if mode:
        out = results_dir / task_id / mode / "results.json"
    else:
        out = results_dir / task_id / "results.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "task_id": task_id,
        "success": success,
        "scores": {"task_score": 2.5 if success else 0.0},
    }
    out.write_text(json.dumps(payload))
    return out


def _write_task_toml(path: Path, task_id: str) -> Path:
    """Write a minimal task.toml."""
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(f"""
[task]
id = "{task_id}"
suite = "customer_escalation"
difficulty = "medium"
session_type = "single"
task_type = "error_provenance"
description = "Test task"
prompt = "Do something."

[[repos]]
url = "github.com/example/repo"
rev = "v1.0.0"
path = "repo"
role = "primary"

[[checkpoints]]
name = "step1"
weight = 1.0
verifier = "checks/check.sh"

[artifacts]
required = []
""")
    return path


# ---------------------------------------------------------------------------
# is_task_completed
# ---------------------------------------------------------------------------

class TestIsTaskCompleted:
    def test_returns_true_when_results_exist_with_success(self, tmp_path: Path) -> None:
        results_dir = tmp_path / "results" / "runs"
        _write_results(results_dir, "task-abc", success=True)
        assert is_task_completed("task-abc", results_dir=results_dir, mode="baseline") is True

    def test_returns_false_when_results_exist_with_failure(self, tmp_path: Path) -> None:
        results_dir = tmp_path / "results" / "runs"
        _write_results(results_dir, "task-abc", success=False)
        assert is_task_completed("task-abc", results_dir=results_dir, mode="baseline") is False

    def test_returns_false_when_no_results_file(self, tmp_path: Path) -> None:
        results_dir = tmp_path / "results" / "runs"
        results_dir.mkdir(parents=True)
        assert is_task_completed("task-missing", results_dir=results_dir, mode="baseline") is False

    def test_returns_false_when_results_dir_absent(self, tmp_path: Path) -> None:
        results_dir = tmp_path / "nonexistent"
        assert is_task_completed("task-abc", results_dir=results_dir, mode="baseline") is False

    def test_returns_false_on_malformed_json(self, tmp_path: Path) -> None:
        results_dir = tmp_path / "results" / "runs"
        f = results_dir / "task-bad" / "results.json"
        f.parent.mkdir(parents=True)
        f.write_text("NOT JSON")
        assert is_task_completed("task-bad", results_dir=results_dir, mode="baseline") is False

    def test_checks_mode_subdirectory(self, tmp_path: Path) -> None:
        results_dir = tmp_path / "results" / "runs"
        _write_results(results_dir, "task-mode", success=True, mode="mcp_only")
        # Should find it when mode matches
        assert is_task_completed("task-mode", results_dir=results_dir, mode="mcp_only") is True
        # Should NOT find it for a different mode (no top-level results.json either)
        assert is_task_completed("task-mode", results_dir=results_dir, mode="baseline") is False


# ---------------------------------------------------------------------------
# filter_completed_tasks
# ---------------------------------------------------------------------------

class TestFilterCompletedTasks:
    def test_removes_completed_tasks(self, tmp_path: Path) -> None:
        results_dir = tmp_path / "results" / "runs"
        _write_results(results_dir, "done-001", success=True)

        tasks = [
            _make_task_info("done-001"),
            _make_task_info("pending-002"),
        ]
        remaining, skipped = filter_completed_tasks(
            tasks, results_dir=results_dir, mode="baseline"
        )
        assert len(remaining) == 1
        assert remaining[0].task_id == "pending-002"
        assert len(skipped) == 1
        assert skipped[0].task_id == "done-001"

    def test_keeps_failed_tasks(self, tmp_path: Path) -> None:
        results_dir = tmp_path / "results" / "runs"
        _write_results(results_dir, "failed-001", success=False)

        tasks = [_make_task_info("failed-001")]
        remaining, skipped = filter_completed_tasks(
            tasks, results_dir=results_dir, mode="baseline"
        )
        assert len(remaining) == 1
        assert len(skipped) == 0

    def test_returns_all_when_no_results(self, tmp_path: Path) -> None:
        results_dir = tmp_path / "results" / "runs"
        results_dir.mkdir(parents=True)
        tasks = [_make_task_info("a"), _make_task_info("b")]
        remaining, skipped = filter_completed_tasks(
            tasks, results_dir=results_dir, mode="baseline"
        )
        assert len(remaining) == 2
        assert len(skipped) == 0


# ---------------------------------------------------------------------------
# CLI parser
# ---------------------------------------------------------------------------

class TestCliParser:
    def test_skip_completed_flag_exists(self) -> None:
        parser = build_parser()
        args = parser.parse_args(["benchmarks/", "--all", "--skip-completed"])
        assert args.skip_completed is True

    def test_skip_completed_default_false(self) -> None:
        parser = build_parser()
        args = parser.parse_args(["benchmarks/", "--all"])
        assert args.skip_completed is False


# ---------------------------------------------------------------------------
# Summary includes skipped count
# ---------------------------------------------------------------------------

class TestSummarySkipCount:
    def test_summary_includes_previously_completed(self, tmp_path: Path) -> None:
        """When --skip-completed is used, summary should show previously_completed count."""
        from run_benchmark import write_summary, TaskResult

        results = [
            TaskResult(task_id="t1", difficulty="medium", status="completed", score=1.0),
        ]
        run_id = "test_run"
        # Monkey-patch PROJECT_ROOT temporarily for write_summary
        import run_benchmark
        original_root = run_benchmark.PROJECT_ROOT
        run_benchmark.PROJECT_ROOT = tmp_path
        try:
            summary_path = write_summary(results, run_id, previously_completed=3)
            data = json.loads(summary_path.read_text())
            assert data["previously_completed"] == 3
        finally:
            run_benchmark.PROJECT_ROOT = original_root


# ---------------------------------------------------------------------------
# TokenBudget
# ---------------------------------------------------------------------------

class TestTokenBudget:
    def test_no_budget_is_unlimited(self) -> None:
        budget = TokenBudget(budget_usd=None, warn_pct=80)
        assert budget.is_exceeded(999.0) is False
        assert budget.should_warn(999.0) is False

    def test_budget_not_exceeded_when_under(self) -> None:
        budget = TokenBudget(budget_usd=10.0, warn_pct=80)
        assert budget.is_exceeded(5.0) is False

    def test_budget_exceeded_when_at_limit(self) -> None:
        budget = TokenBudget(budget_usd=10.0, warn_pct=80)
        assert budget.is_exceeded(10.0) is True

    def test_budget_exceeded_when_over(self) -> None:
        budget = TokenBudget(budget_usd=10.0, warn_pct=80)
        assert budget.is_exceeded(15.0) is True

    def test_warn_triggers_at_threshold(self) -> None:
        budget = TokenBudget(budget_usd=10.0, warn_pct=80)
        assert budget.should_warn(7.99) is False
        assert budget.should_warn(8.0) is True
        assert budget.should_warn(9.5) is True

    def test_warn_not_triggered_when_no_budget(self) -> None:
        budget = TokenBudget(budget_usd=None, warn_pct=80)
        assert budget.should_warn(100.0) is False

    def test_remaining_usd(self) -> None:
        budget = TokenBudget(budget_usd=10.0, warn_pct=80)
        assert budget.remaining_usd(3.5) == pytest.approx(6.5)

    def test_remaining_usd_no_budget(self) -> None:
        budget = TokenBudget(budget_usd=None, warn_pct=80)
        assert budget.remaining_usd(100.0) == float("inf")

    def test_pct_used(self) -> None:
        budget = TokenBudget(budget_usd=10.0, warn_pct=80)
        assert budget.pct_used(2.5) == pytest.approx(25.0)

    def test_pct_used_no_budget(self) -> None:
        budget = TokenBudget(budget_usd=None, warn_pct=80)
        assert budget.pct_used(100.0) == 0.0


class TestTokenBudgetCli:
    def test_budget_usd_flag_exists(self) -> None:
        parser = build_parser()
        args = parser.parse_args(["benchmarks/", "--all", "--budget-usd", "25.0"])
        assert args.budget_usd == 25.0

    def test_budget_usd_default_none(self) -> None:
        parser = build_parser()
        args = parser.parse_args(["benchmarks/", "--all"])
        assert args.budget_usd is None

    def test_budget_warn_pct_flag(self) -> None:
        parser = build_parser()
        args = parser.parse_args(["benchmarks/", "--all", "--budget-usd", "10", "--budget-warn-pct", "70"])
        assert args.budget_warn_pct == 70

    def test_budget_warn_pct_default(self) -> None:
        parser = build_parser()
        args = parser.parse_args(["benchmarks/", "--all"])
        assert args.budget_warn_pct == 80


class TestTokenBudgetInSummary:
    def test_summary_includes_cost_tracking(self, tmp_path: Path) -> None:
        results = [
            TaskResult(task_id="t1", difficulty="medium", status="completed", score=1.0),
        ]
        import run_benchmark
        original_root = run_benchmark.PROJECT_ROOT
        run_benchmark.PROJECT_ROOT = tmp_path
        try:
            summary_path = write_summary(
                results, "test_run",
                previously_completed=0,
                cumulative_cost_usd=5.25,
                budget_usd=10.0,
            )
            data = json.loads(summary_path.read_text())
            assert data["cumulative_cost_usd"] == pytest.approx(5.25)
            assert data["budget_usd"] == 10.0
        finally:
            run_benchmark.PROJECT_ROOT = original_root

    def test_summary_omits_budget_when_none(self, tmp_path: Path) -> None:
        results = [
            TaskResult(task_id="t1", difficulty="medium", status="completed", score=1.0),
        ]
        import run_benchmark
        original_root = run_benchmark.PROJECT_ROOT
        run_benchmark.PROJECT_ROOT = tmp_path
        try:
            summary_path = write_summary(results, "test_run")
            data = json.loads(summary_path.read_text())
            assert data["cumulative_cost_usd"] == 0.0
            assert data.get("budget_usd") is None
        finally:
            run_benchmark.PROJECT_ROOT = original_root


class TestExtractCostFromResult:
    """Test that cost is extracted from results.json tool_usage after task runs."""

    def test_extract_cost_from_results_json(self, tmp_path: Path) -> None:
        from run_benchmark import extract_task_cost

        results_dir = tmp_path / "results" / "runs" / "task-001"
        results_dir.mkdir(parents=True)
        results_file = results_dir / "results.json"
        results_file.write_text(json.dumps({
            "task_id": "task-001",
            "success": True,
            "tool_usage": {
                "total_input_tokens": 50000,
                "total_output_tokens": 10000,
                "cost_usd": 1.23,
            },
        }))
        assert extract_task_cost("task-001", results_dir=tmp_path / "results" / "runs") == pytest.approx(1.23)

    def test_extract_cost_missing_results(self, tmp_path: Path) -> None:
        from run_benchmark import extract_task_cost

        assert extract_task_cost("task-missing", results_dir=tmp_path / "results" / "runs") == 0.0

    def test_extract_cost_no_tool_usage(self, tmp_path: Path) -> None:
        from run_benchmark import extract_task_cost

        results_dir = tmp_path / "results" / "runs" / "task-001"
        results_dir.mkdir(parents=True)
        results_file = results_dir / "results.json"
        results_file.write_text(json.dumps({
            "task_id": "task-001",
            "success": True,
        }))
        assert extract_task_cost("task-001", results_dir=tmp_path / "results" / "runs") == 0.0

    def test_extract_cost_with_mode_subdirectory(self, tmp_path: Path) -> None:
        from run_benchmark import extract_task_cost

        results_dir = tmp_path / "results" / "runs" / "task-001" / "mcp_only"
        results_dir.mkdir(parents=True)
        results_file = results_dir / "results.json"
        results_file.write_text(json.dumps({
            "task_id": "task-001",
            "success": True,
            "tool_usage": {"cost_usd": 2.50},
        }))
        assert extract_task_cost(
            "task-001",
            results_dir=tmp_path / "results" / "runs",
            mode="mcp_only",
        ) == pytest.approx(2.50)


# ---------------------------------------------------------------------------
# Manifest filter (curated slice dispatch)
# ---------------------------------------------------------------------------

def test_manifest_task_ids_strips_the_suite_qualifier(tmp_path: Path) -> None:
    manifest = tmp_path / "slice.json"
    manifest.write_text(
        json.dumps({"tasks": ["incident_response/opa-001", "technical_debt/babel-001"]})
    )
    assert manifest_task_ids(manifest) == {"opa-001", "babel-001"}


def test_filter_tasks_keeps_only_manifest_members() -> None:
    tasks = [_make_task_info("a-001"), _make_task_info("b-002"), _make_task_info("c-003")]
    kept = filter_tasks(tasks, task_ids={"a-001", "c-003"})
    assert [t.task_id for t in kept] == ["a-001", "c-003"]


def test_filter_tasks_without_a_manifest_keeps_everything() -> None:
    tasks = [_make_task_info("a-001"), _make_task_info("b-002")]
    assert len(filter_tasks(tasks, task_ids=None)) == 2


def test_manifest_naming_a_task_that_does_not_exist_yields_nothing() -> None:
    """Manifest drift must show as an empty run, not a silent full-corpus run."""
    tasks = [_make_task_info("a-001")]
    assert filter_tasks(tasks, task_ids={"absent-999"}) == []


def test_manifest_filter_composes_with_the_other_filters() -> None:
    tasks = [
        _make_task_info("a-001", difficulty="hard"),
        _make_task_info("b-002", difficulty="medium"),
    ]
    kept = filter_tasks(tasks, task_ids={"a-001", "b-002"}, difficulty="hard")
    assert [t.task_id for t in kept] == ["a-001"]


class TestRetiredAccountsAreNotDispatchedTo:
    """A retired account must leave the round-robin, not just look unhealthy.

    account2's organization has Claude Code subscription access disabled. Its
    OAuth token still validates, so check_infra.py reported it ready while
    every run assigned to it died on an HTTP 403 in under ten seconds. Left in
    a five-account round-robin that silently costs every fifth task in a
    sweep, and the losses read as task failures rather than as one dead
    account.
    """

    def test_a_retired_account_is_dropped_from_a_range(self, monkeypatch) -> None:
        monkeypatch.setattr(run_benchmark, "retired_accounts", lambda: frozenset({"account2"}))

        assert run_benchmark.parse_accounts("1-5") == [1, 3, 4, 5]

    def test_live_accounts_are_untouched(self, monkeypatch) -> None:
        monkeypatch.setattr(run_benchmark, "retired_accounts", lambda: frozenset({"account2"}))

        assert run_benchmark.parse_accounts("1,3,5") == [1, 3, 5]
        assert run_benchmark.parse_accounts("4") == [4]

    def test_an_all_retired_spec_refuses_rather_than_running_nothing(
        self, monkeypatch
    ) -> None:
        """Returning [] here would dispatch with no account at all."""
        monkeypatch.setattr(run_benchmark, "retired_accounts", lambda: frozenset({"account2"}))

        with pytest.raises(SystemExit, match="retired"):
            run_benchmark.parse_accounts("2")

    def test_no_account_spec_still_means_no_accounts(self, monkeypatch) -> None:
        monkeypatch.setattr(run_benchmark, "retired_accounts", lambda: frozenset({"account2"}))

        assert run_benchmark.parse_accounts(None) == []

    def test_account2_is_retired_in_the_shipped_config(self) -> None:
        """The declaration itself, not a fixture of it."""
        assert "account2" in run_benchmark.retired_accounts()


class TestRetiredAccountsAreInvisibleToReadiness:
    """Readiness output must not advertise capacity that 403s on first call.

    account2's token validates indefinitely, so the token-expiry checks in
    check_infra reported it healthy and counted its six slots as available
    right up to the sweep that lost ten minutes to it.
    """

    def test_discovery_skips_retired_account_homes(self, tmp_path) -> None:
        import sys

        sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts" / "infra"))
        import account_health

        homes = tmp_path / ".claude-homes"
        for name in ("account1", "account2", "account3"):
            (homes / name).mkdir(parents=True)

        with patch.object(account_health, "_retired_accounts", return_value=frozenset({"account2"})):
            discovered = [path.name for path in account_health.discover_managed_account_homes(tmp_path)]

        assert discovered == ["account1", "account3"]
