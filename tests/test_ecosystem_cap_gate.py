"""Tests for scripts/validation/ecosystem_cap_gate.py."""

from __future__ import annotations

import sys
import textwrap
from pathlib import Path

import pytest

sys.path.insert(
    0, str(Path(__file__).resolve().parent.parent / "scripts" / "validation")
)
import ecosystem_cap_gate as gate  # noqa: E402


# ── Fixtures ──────────────────────────────────────────────────────


def _write_task(
    benchmarks_dir: Path,
    task_id: str,
    *,
    suite: str = "incident_response",
    stratum: str = "dual_repo",
    languages: tuple[str, ...] = ("go",),
    frameworks: tuple[str, ...] = ("etcd",),
) -> Path:
    """Create a minimal task.toml that the gate can parse."""
    task_dir = benchmarks_dir / suite / task_id
    task_dir.mkdir(parents=True, exist_ok=True)
    langs = ", ".join(f'"{lang}"' for lang in languages)
    frames = ", ".join(f'"{f}"' for f in frameworks)
    body = textwrap.dedent(
        f"""\
        difficulty_stratum = "{stratum}"

        [task]
        id = "{task_id}"
        suite = "{suite}"
        task_type = "incident_investigation"

        [metadata]
        languages = [{langs}]
        frameworks = [{frames}]
        """
    )
    (task_dir / "task.toml").write_text(body)
    return task_dir


def _seed_corpus(
    benchmarks_dir: Path, *, go_count: int, python_count: int
) -> None:
    """Create N Go-tagged + M Python-tagged dual-repo tasks."""
    benchmarks_dir.mkdir(parents=True, exist_ok=True)
    for i in range(go_count):
        _write_task(
            benchmarks_dir,
            f"go-task-{i:03d}",
            languages=("go",),
            frameworks=(f"go-fw-{i}",),
        )
    for i in range(python_count):
        _write_task(
            benchmarks_dir,
            f"py-task-{i:03d}",
            languages=("python",),
            frameworks=(f"py-fw-{i}",),
        )


# ── Distribution computation ──────────────────────────────────────


def test_distribution_counts_only_multi_repo(tmp_path: Path) -> None:
    benchmarks = tmp_path / "benchmarks"
    benchmarks.mkdir()
    _write_task(benchmarks, "go-mr", stratum="dual_repo", languages=("go",))
    _write_task(
        benchmarks, "go-single", stratum="large_single", languages=("go",)
    )
    _write_task(
        benchmarks, "py-cal", stratum="calibration", languages=("python",)
    )

    tasks = gate.load_tasks(benchmarks)
    dist = gate.compute_distribution(tasks)

    assert dist.multi_repo_total == 1
    assert dist.ecosystem_counts.get("go") == 1
    assert dist.ecosystem_counts.get("python", 0) == 0


def test_distribution_archived_tasks_skipped(tmp_path: Path) -> None:
    benchmarks = tmp_path / "benchmarks"
    benchmarks.mkdir()
    _write_task(benchmarks, "live", stratum="dual_repo", languages=("go",))

    archived_dir = benchmarks / "_archived" / "old-task"
    archived_dir.mkdir(parents=True)
    (archived_dir / "task.toml").write_text(
        'difficulty_stratum = "dual_repo"\n'
        '[task]\nid = "old"\n[metadata]\nlanguages = ["go"]\n'
    )

    tasks = gate.load_tasks(benchmarks)
    assert {t.task_id for t in tasks} == {"live"}


# ── Gate behavior — bead acceptance criteria ──────────────────────


def test_gate_blocks_when_share_rises_over_cap(tmp_path: Path) -> None:
    """Bead AC: 'attempting to add a 12th Go task — gate fires'."""
    baseline = tmp_path / "before" / "benchmarks"
    candidate = tmp_path / "after" / "benchmarks"

    # Construct a baseline that is right at the 40% cap: 11 Go / 17 Python -> 11/28 ≈ 39.3%
    _seed_corpus(baseline, go_count=11, python_count=17)
    _seed_corpus(candidate, go_count=12, python_count=17)

    result = gate.run_gate(baseline, candidate, {"go": 0.40})

    assert not result.passes
    assert len(result.violations) == 1
    v = result.violations[0]
    assert v.ecosystem == "go"
    assert v.before_count == 11
    assert v.after_count == 12
    assert v.after_share > 0.40
    assert "raises ecosystem share" in v.reason


def test_gate_passes_when_non_go_added(tmp_path: Path) -> None:
    """Bead AC: 'attempting to add a non-Go task — gate passes'."""
    baseline = tmp_path / "before" / "benchmarks"
    candidate = tmp_path / "after" / "benchmarks"

    # Already over cap: 12 Go / 17 Python -> 12/29 ≈ 41.4%
    _seed_corpus(baseline, go_count=12, python_count=17)
    # Add a Python task; Go count flat, share dilutes to 12/30 = 40%
    _seed_corpus(candidate, go_count=12, python_count=18)

    result = gate.run_gate(baseline, candidate, {"go": 0.40})

    assert result.passes, (
        f"expected pass; violations: {result.violations}"
    )


def test_gate_passes_when_already_over_cap_but_unchanged(
    tmp_path: Path,
) -> None:
    """Legacy over-cap state must not block unrelated commits."""
    baseline = tmp_path / "before" / "benchmarks"
    candidate = tmp_path / "after" / "benchmarks"

    _seed_corpus(baseline, go_count=15, python_count=10)  # 60% Go, over cap
    _seed_corpus(candidate, go_count=15, python_count=10)

    result = gate.run_gate(baseline, candidate, {"go": 0.40})

    assert result.passes


def test_gate_passes_when_share_decreases_even_if_count_grows(
    tmp_path: Path,
) -> None:
    """Mass-rebalance commits that add some Go but more non-Go must pass."""
    baseline = tmp_path / "before" / "benchmarks"
    candidate = tmp_path / "after" / "benchmarks"

    _seed_corpus(baseline, go_count=15, python_count=10)  # 60% Go
    _seed_corpus(candidate, go_count=16, python_count=20)  # 16/36 ≈ 44.4%

    result = gate.run_gate(baseline, candidate, {"go": 0.40})

    assert result.passes


def test_gate_blocks_when_non_go_removed_pushes_share_up(
    tmp_path: Path,
) -> None:
    """Removing a non-Go multi-repo task while over cap is also a worsening."""
    baseline = tmp_path / "before" / "benchmarks"
    candidate = tmp_path / "after" / "benchmarks"

    _seed_corpus(baseline, go_count=12, python_count=15)  # 12/27 ≈ 44.4%
    _seed_corpus(candidate, go_count=12, python_count=14)  # 12/26 ≈ 46.2%

    result = gate.run_gate(baseline, candidate, {"go": 0.40})

    assert not result.passes
    assert result.violations[0].ecosystem == "go"


def test_gate_passes_when_baseline_under_cap_and_addition_stays_under(
    tmp_path: Path,
) -> None:
    baseline = tmp_path / "before" / "benchmarks"
    candidate = tmp_path / "after" / "benchmarks"

    _seed_corpus(baseline, go_count=8, python_count=20)  # 8/28 ≈ 28.6%
    _seed_corpus(candidate, go_count=9, python_count=20)  # 9/29 ≈ 31.0%

    result = gate.run_gate(baseline, candidate, {"go": 0.40})

    assert result.passes


def test_gate_blocks_when_fresh_addition_pushes_baseline_over_cap(
    tmp_path: Path,
) -> None:
    """Bead-style scenario: from 40.7% to 46.2% via Go-only adds."""
    baseline = tmp_path / "before" / "benchmarks"
    candidate = tmp_path / "after" / "benchmarks"

    # baseline: 44 Go / 64 Python -> 44/108 ≈ 40.7%
    _seed_corpus(baseline, go_count=44, python_count=64)
    # candidate: +11 Go (the 0rv.azt scenario) -> 55/119 ≈ 46.2%
    _seed_corpus(candidate, go_count=55, python_count=64)

    result = gate.run_gate(baseline, candidate, {"go": 0.40})

    assert not result.passes
    v = result.violations[0]
    assert pytest.approx(v.before_share, abs=1e-3) == 44 / 108
    assert pytest.approx(v.after_share, abs=1e-3) == 55 / 119


# ── Cap parsing & CLI ─────────────────────────────────────────────


def test_parse_caps_default() -> None:
    assert gate._parse_caps(None) == {"go": 0.40}


def test_parse_caps_override() -> None:
    assert gate._parse_caps("go=0.40,python=0.30") == {
        "go": 0.40,
        "python": 0.30,
    }


def test_parse_caps_invalid_raises() -> None:
    with pytest.raises(ValueError):
        gate._parse_caps("badspec")


def test_main_returns_nonzero_on_violation(tmp_path: Path) -> None:
    baseline = tmp_path / "before" / "benchmarks"
    candidate = tmp_path / "after" / "benchmarks"
    _seed_corpus(baseline, go_count=11, python_count=17)
    _seed_corpus(candidate, go_count=12, python_count=17)

    rc = gate.main(
        [
            "--baseline-tasks-dir",
            str(baseline),
            "--candidate-tasks-dir",
            str(candidate),
        ]
    )
    assert rc == 1


def test_main_returns_zero_on_pass(tmp_path: Path) -> None:
    baseline = tmp_path / "before" / "benchmarks"
    candidate = tmp_path / "after" / "benchmarks"
    _seed_corpus(baseline, go_count=12, python_count=17)
    _seed_corpus(candidate, go_count=12, python_count=18)

    rc = gate.main(
        [
            "--baseline-tasks-dir",
            str(baseline),
            "--candidate-tasks-dir",
            str(candidate),
        ]
    )
    assert rc == 0
