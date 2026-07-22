"""Tests for scripts/run_sweep.py."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

# Import the module under test
import sys

_SCRIPTS_DIR = Path(__file__).resolve().parent.parent / "scripts"
sys.path.insert(0, str(_SCRIPTS_DIR))

from run_sweep import (
    ALL_MODES,
    DEFAULT_SWEEP_MODES,
    MATRIX_PRESETS,
    SweepItem,
    build_parser,
    build_sweep_matrix,
    check_completion,
    generate_manifest,
    generate_run_commands,
    main,
)
from run_benchmark import TaskInfo

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


def _make_task(
    task_id: str = "test-task-001",
    suite: str = "customer_escalation",
    difficulty: str = "medium",
    session_type: str = "single",
    task_type: str = "error_provenance",
    paper_scope: str = "included",
) -> TaskInfo:
    return TaskInfo(
        task_id=task_id,
        suite=suite,
        difficulty=difficulty,
        session_type=session_type,
        task_type=task_type,
        toml_path=Path(f"/fake/benchmarks/{suite}/{task_id}/task.toml"),
        paper_scope=paper_scope,
    )


# ---------------------------------------------------------------------------
# build_sweep_matrix
# ---------------------------------------------------------------------------


class TestBuildSweepMatrix:
    def test_default_modes(self) -> None:
        tasks = [_make_task("t1"), _make_task("t2")]
        items = build_sweep_matrix(tasks)
        assert len(items) == 6  # 2 tasks x 3 modes

    def test_custom_modes(self) -> None:
        tasks = [_make_task("t1")]
        items = build_sweep_matrix(tasks, modes=["baseline", "hybrid"])
        assert len(items) == 2
        modes = {item.mode for item in items}
        assert modes == {"baseline", "hybrid"}

    def test_preserves_task_metadata(self) -> None:
        task = _make_task("abc-001", suite="security_operations", difficulty="hard")
        items = build_sweep_matrix([task], modes=["mcp_only"])
        assert len(items) == 1
        item = items[0]
        assert item.task_id == "abc-001"
        assert item.suite == "security_operations"
        assert item.difficulty == "hard"
        assert item.mode == "mcp_only"
        assert item.status == "pending"
        assert item.results_path is None

    def test_empty_tasks(self) -> None:
        items = build_sweep_matrix([])
        assert items == []

    def test_matrix_all_keeps_excluded_tasks(self) -> None:
        tasks = [_make_task("t1"), _make_task("t2", paper_scope="excluded")]
        items = build_sweep_matrix(tasks, modes=["baseline"], matrix="all")
        assert {item.task_id for item in items} == {"t1", "t2"}

    def test_matrix_publish_drops_excluded_tasks(self) -> None:
        tasks = [
            _make_task("t1"),
            _make_task("t2", paper_scope="excluded"),
            _make_task("t3"),
        ]
        items = build_sweep_matrix(tasks, modes=["baseline"], matrix="publish")
        assert {item.task_id for item in items} == {"t1", "t3"}

    def test_matrix_publish_default_included_task_kept(self) -> None:
        # paper_scope defaults to "included" when a task.toml omits the field.
        task = _make_task("t1")
        assert task.paper_scope == "included"
        items = build_sweep_matrix([task], modes=["baseline"], matrix="publish")
        assert len(items) == 1

    def test_unknown_matrix_scope_raises(self) -> None:
        with pytest.raises(ValueError, match="Unknown matrix scope"):
            build_sweep_matrix([_make_task("t1")], matrix="bogus")

    def test_empty_modes(self) -> None:
        items = build_sweep_matrix([_make_task()], modes=[])
        assert items == []

    def test_all_items_are_frozen(self) -> None:
        items = build_sweep_matrix([_make_task()], modes=["baseline"])
        with pytest.raises(AttributeError):
            items[0].status = "scored"  # type: ignore[misc]


# ---------------------------------------------------------------------------
# check_completion
# ---------------------------------------------------------------------------


class TestCheckCompletion:
    def test_scored_single_mode_layout(self, tmp_path: Path) -> None:
        """results/<task_id>/results.json with success: true -> scored."""
        task_dir = tmp_path / "test-task-001"
        task_dir.mkdir()
        (task_dir / "results.json").write_text(json.dumps({"success": True}))

        items = build_sweep_matrix([_make_task("test-task-001")], modes=["baseline"])
        result = check_completion(items, results_dirs=[tmp_path])

        assert len(result) == 1
        assert result[0].status == "scored"
        assert result[0].results_path is not None

    def test_scored_multi_mode_layout(self, tmp_path: Path) -> None:
        """results/<task_id>/<mode>/results.json with success: true -> scored."""
        mode_dir = tmp_path / "test-task-001" / "mcp_only"
        mode_dir.mkdir(parents=True)
        (mode_dir / "results.json").write_text(json.dumps({"success": True}))

        items = build_sweep_matrix([_make_task("test-task-001")], modes=["mcp_only"])
        result = check_completion(items, results_dirs=[tmp_path])

        assert result[0].status == "scored"

    def test_scored_mcp_batch_layout(self, tmp_path: Path) -> None:
        """results/mcp_batch*/<task_id>_<mode>/results.json -> scored."""
        batch_dir = tmp_path / "test-task-001_hybrid"
        batch_dir.mkdir()
        (batch_dir / "results.json").write_text(json.dumps({"success": True}))

        items = build_sweep_matrix([_make_task("test-task-001")], modes=["hybrid"])
        result = check_completion(items, results_dirs=[tmp_path])

        assert result[0].status == "scored"

    def test_failed_result(self, tmp_path: Path) -> None:
        """results.json with success: false -> failed."""
        task_dir = tmp_path / "test-task-001"
        task_dir.mkdir()
        (task_dir / "results.json").write_text(json.dumps({"success": False}))

        items = build_sweep_matrix([_make_task("test-task-001")], modes=["baseline"])
        result = check_completion(items, results_dirs=[tmp_path])

        assert result[0].status == "failed"
        assert result[0].results_path is not None

    def test_pending_no_results(self, tmp_path: Path) -> None:
        """No results files at all -> pending."""
        items = build_sweep_matrix([_make_task("test-task-001")], modes=["baseline"])
        result = check_completion(items, results_dirs=[tmp_path])

        assert result[0].status == "pending"
        assert result[0].results_path is None

    def test_malformed_json(self, tmp_path: Path) -> None:
        """Malformed results.json -> pending (treated as if not found)."""
        task_dir = tmp_path / "test-task-001"
        task_dir.mkdir()
        (task_dir / "results.json").write_text("not json")

        items = build_sweep_matrix([_make_task("test-task-001")], modes=["baseline"])
        result = check_completion(items, results_dirs=[tmp_path])

        assert result[0].status == "pending"

    def test_scored_takes_priority_over_failed(self, tmp_path: Path) -> None:
        """If one dir has scored and another has failed, scored wins."""
        dir_a = tmp_path / "a"
        dir_b = tmp_path / "b"

        # Dir A: failed
        failed_dir = dir_a / "test-task-001"
        failed_dir.mkdir(parents=True)
        (failed_dir / "results.json").write_text(json.dumps({"success": False}))

        # Dir B: scored (mcp_batch layout)
        scored_dir = dir_b / "test-task-001_baseline"
        scored_dir.mkdir(parents=True)
        (scored_dir / "results.json").write_text(json.dumps({"success": True}))

        items = build_sweep_matrix([_make_task("test-task-001")], modes=["baseline"])
        result = check_completion(items, results_dirs=[dir_a, dir_b])

        assert result[0].status == "scored"

    def test_multiple_tasks_and_modes(self, tmp_path: Path) -> None:
        """Check a matrix of 2 tasks x 2 modes with mode-specific layouts."""
        # t1 baseline: scored (multi-mode layout)
        (tmp_path / "t1" / "baseline").mkdir(parents=True)
        (tmp_path / "t1" / "baseline" / "results.json").write_text(
            json.dumps({"success": True})
        )
        # t2 hybrid: failed (mcp_batch layout)
        (tmp_path / "t2_hybrid").mkdir()
        (tmp_path / "t2_hybrid" / "results.json").write_text(
            json.dumps({"success": False})
        )

        tasks = [_make_task("t1"), _make_task("t2")]
        items = build_sweep_matrix(tasks, modes=["baseline", "hybrid"])
        result = check_completion(items, results_dirs=[tmp_path])

        status_map = {(r.task_id, r.mode): r.status for r in result}
        assert status_map[("t1", "baseline")] == "scored"
        assert status_map[("t1", "hybrid")] == "pending"
        assert status_map[("t2", "baseline")] == "pending"
        assert status_map[("t2", "hybrid")] == "failed"


# ---------------------------------------------------------------------------
# generate_manifest
# ---------------------------------------------------------------------------


class TestGenerateManifest:
    def test_structure(self) -> None:
        items = [
            SweepItem("t1", "baseline", "s1", "easy", "single", "/p", "scored", "/r"),
            SweepItem("t1", "mcp_only", "s1", "easy", "single", "/p", "pending", None),
            SweepItem("t2", "baseline", "s2", "hard", "single", "/p", "failed", "/f"),
        ]
        manifest = generate_manifest(items)

        assert manifest["total_combinations"] == 3
        assert manifest["by_status"]["scored"] == 1
        assert manifest["by_status"]["pending"] == 1
        assert manifest["by_status"]["failed"] == 1
        assert manifest["by_mode"]["baseline"]["scored"] == 1
        assert manifest["by_mode"]["baseline"]["failed"] == 1
        assert manifest["by_mode"]["mcp_only"]["pending"] == 1
        assert len(manifest["items"]) == 3
        assert "generated_at" in manifest

    def test_empty_items(self) -> None:
        manifest = generate_manifest([])
        assert manifest["total_combinations"] == 0
        assert manifest["by_status"] == {}
        assert manifest["by_mode"] == {}
        assert manifest["items"] == []

    def test_item_fields(self) -> None:
        items = [
            SweepItem(
                "t1", "hybrid", "suite_a", "medium", "chain", "/p", "pending", None
            ),
        ]
        manifest = generate_manifest(items)
        item = manifest["items"][0]
        assert item["task_id"] == "t1"
        assert item["mode"] == "hybrid"
        assert item["suite"] == "suite_a"
        assert item["difficulty"] == "medium"
        assert item["status"] == "pending"
        assert item["results_path"] is None

    def test_manifest_is_json_serializable(self) -> None:
        items = [
            SweepItem("t1", "baseline", "s", "easy", "single", "/p", "scored", "/r"),
        ]
        manifest = generate_manifest(items)
        # Should not raise
        json.dumps(manifest)


# ---------------------------------------------------------------------------
# generate_run_commands
# ---------------------------------------------------------------------------


class TestGenerateRunCommands:
    def test_single_mode(self) -> None:
        items = [
            SweepItem("t1", "baseline", "s", "e", "single", "/p", "pending", None),
            SweepItem("t2", "baseline", "s", "e", "single", "/p", "pending", None),
        ]
        commands = generate_run_commands(items, accounts="1-5")
        assert len(commands) == 1
        assert "--mode baseline" in commands[0]
        assert "--account 1-5" in commands[0]

    def test_multiple_modes(self) -> None:
        items = [
            SweepItem("t1", "baseline", "s", "e", "single", "/p", "pending", None),
            SweepItem("t1", "hybrid", "s", "e", "single", "/p", "pending", None),
        ]
        commands = generate_run_commands(items, accounts="1,3")
        assert len(commands) == 2
        modes_in_cmds = {c.split("--mode ")[1] for c in commands}
        assert modes_in_cmds == {"baseline", "hybrid"}

    def test_ignores_non_pending(self) -> None:
        items = [
            SweepItem("t1", "baseline", "s", "e", "single", "/p", "scored", "/r"),
            SweepItem("t1", "mcp_only", "s", "e", "single", "/p", "failed", "/f"),
        ]
        commands = generate_run_commands(items)
        assert commands == []

    def test_no_pending(self) -> None:
        commands = generate_run_commands([])
        assert commands == []

    def test_model_flag_appended_when_given(self) -> None:
        items = [SweepItem("t1", "baseline", "s", "e", "single", "/p", "pending", None)]
        commands = generate_run_commands(items, model="claude-sonnet-5")
        assert len(commands) == 1
        assert "--model claude-sonnet-5" in commands[0]

    def test_model_flag_absent_by_default(self) -> None:
        """Do not change default behavior when --model is absent."""
        items = [SweepItem("t1", "baseline", "s", "e", "single", "/p", "pending", None)]
        commands = generate_run_commands(items)
        assert "--model" not in commands[0]


# ---------------------------------------------------------------------------
# --matrix publish
# ---------------------------------------------------------------------------


class TestMatrixPreset:
    def test_publish_matrix_is_baseline_mcp_only_cli(self) -> None:
        assert MATRIX_PRESETS["publish"] == ("baseline", "mcp_only", "cli")

    def test_default_matrix_unchanged_without_flags(self) -> None:
        parser = build_parser()
        args = parser.parse_args(["--manifest-only"])
        assert args.modes is None
        assert args.matrix is None
        # main() must fall back to DEFAULT_SWEEP_MODES, not silently change it
        assert DEFAULT_SWEEP_MODES == ("baseline", "mcp_only", "hybrid")

    def test_matrix_and_modes_mutually_exclusive(self, tmp_path: Path, monkeypatch) -> None:
        import run_sweep

        monkeypatch.setattr(run_sweep, "discover_all_tasks", lambda: [])
        output = tmp_path / "manifest.json"
        rc = main(
            [
                "--matrix",
                "publish",
                "--modes",
                "baseline,cli",
                "--manifest-only",
                "--output",
                str(output),
            ]
        )
        assert rc == 1

    def test_matrix_publish_drives_the_sweep(self, tmp_path: Path, monkeypatch) -> None:
        """--matrix publish must actually select {baseline, mcp_only, cli} for
        the sweep matrix — not just parse — verified via the written
        manifest's by_mode keys, no hybrid."""
        import run_sweep

        fake_task = _make_task("task-001")
        monkeypatch.setattr(run_sweep, "discover_all_tasks", lambda: [fake_task])
        monkeypatch.setattr(run_sweep, "_find_results_dirs", lambda extra_dirs=None: [])
        output = tmp_path / "manifest.json"
        rc = main(
            [
                "--matrix",
                "publish",
                "--manifest-only",
                "--output",
                str(output),
            ]
        )
        assert rc == 0
        manifest = json.loads(output.read_text())
        assert manifest["total_combinations"] == 3
        assert set(manifest["by_mode"].keys()) == {"baseline", "mcp_only", "cli"}

    def test_invalid_matrix_choice_rejected_by_argparse(self) -> None:
        parser = build_parser()
        with pytest.raises(SystemExit):
            parser.parse_args(["--matrix", "not-a-real-matrix"])


class TestExclusiveResults:
    """--exclusive-results must scan ONLY the given dirs — the default
    results/mcp_batch* glob banks cells scored by pre-fix code, which the
    publish sweep's data-hygiene rule forbids."""

    def test_exclusive_returns_only_given_dirs(self, tmp_path, monkeypatch):
        import run_sweep as rs

        fresh = tmp_path / "fresh"
        fresh.mkdir()
        stale_root = tmp_path / "results"
        (stale_root / "mcp_batch").mkdir(parents=True)
        (stale_root / "runs").mkdir()
        monkeypatch.setattr(rs, "PROJECT_ROOT", tmp_path)

        exclusive = rs.resolve_results_dirs(True, [fresh])
        assert exclusive == [fresh.resolve()]

        default = rs.resolve_results_dirs(False, [fresh])
        assert (stale_root / "runs").resolve() in [d.resolve() for d in default]
        assert (stale_root / "mcp_batch").resolve() in [
            d.resolve() for d in default
        ]

    def test_exclusive_without_dir_raises(self):
        import pytest
        import run_sweep as rs

        with pytest.raises(ValueError):
            rs.resolve_results_dirs(True, None)

    def test_flag_is_wired(self):
        import run_sweep as rs

        args = rs.build_parser().parse_args(
            ["--exclusive-results", "--results-dir", "x"]
        )
        assert args.exclusive_results and args.results_dirs == ["x"]
