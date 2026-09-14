"""Tests for scripts/validation/emit_oracle_consensus.py.

Cross-rig provenance shim from bead codeprobe-zf3k. Pins the mapping from
EnterpriseBench's per-file ``source`` field
(``deterministic`` | ``curator`` | ``both``) onto the canonical
``oracle_backends_consensus`` list shape that codeprobe
``core/bias_detection.py`` reads.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
SCRIPTS_DIR = REPO_ROOT / "scripts" / "validation"
sys.path.insert(0, str(SCRIPTS_DIR))

import emit_oracle_consensus as mod  # noqa: E402


# ---- consensus_from_sources ---------------------------------------------


def test_consensus_from_deterministic_only() -> None:
    assert mod.consensus_from_sources(["deterministic"]) == ("deterministic",)


def test_consensus_from_curator_only() -> None:
    assert mod.consensus_from_sources(["curator"]) == ("curator",)


def test_consensus_from_both_yields_union() -> None:
    assert mod.consensus_from_sources(["both"]) == ("curator", "deterministic")


def test_consensus_from_mixed_dedup_and_sort() -> None:
    sources = ["deterministic", "curator", "deterministic", "both"]
    assert mod.consensus_from_sources(sources) == ("curator", "deterministic")


def test_consensus_ignores_unknown_labels() -> None:
    assert mod.consensus_from_sources(["mystery"]) == ()
    assert mod.consensus_from_sources(["deterministic", "mystery"]) == (
        "deterministic",
    )


def test_consensus_empty_input() -> None:
    assert mod.consensus_from_sources([]) == ()


# ---- process_task -------------------------------------------------------


def _write_gt(task_dir: Path, gt: dict) -> Path:
    task_dir.mkdir(parents=True, exist_ok=True)
    gt_path = task_dir / "ground_truth.json"
    gt_path.write_text(json.dumps(gt, indent=2) + "\n", encoding="utf-8")
    return gt_path


def test_process_task_adds_consensus_field(tmp_path: Path) -> None:
    """A typed required-files entry produces a `["deterministic"]` list."""
    gt_path = _write_gt(
        tmp_path / "task",
        {
            "task_id": "t1",
            "required_files": [
                {"path": "a.py", "repo": "demo", "source": "deterministic"}
            ],
        },
    )
    outcome = mod.process_task(tmp_path / "task")
    assert outcome.action == "updated"
    assert outcome.consensus == ("deterministic",)
    written = json.loads(gt_path.read_text(encoding="utf-8"))
    assert written["oracle_backends_consensus"] == ["deterministic"]


def test_process_task_handles_both_source(tmp_path: Path) -> None:
    """``source: both`` expands to the curator+deterministic pair."""
    _write_gt(
        tmp_path / "task",
        {
            "task_id": "t1",
            "required_files": [
                {"path": "a.py", "repo": "demo", "source": "both"}
            ],
        },
    )
    outcome = mod.process_task(tmp_path / "task")
    assert outcome.action == "updated"
    assert outcome.consensus == ("curator", "deterministic")


def test_process_task_idempotent(tmp_path: Path) -> None:
    """Re-running on an already-updated file reports unchanged."""
    _write_gt(
        tmp_path / "task",
        {
            "task_id": "t1",
            "required_files": [
                {"path": "a.py", "repo": "demo", "source": "deterministic"}
            ],
            "oracle_backends_consensus": ["deterministic"],
        },
    )
    outcome = mod.process_task(tmp_path / "task")
    assert outcome.action == "unchanged"


def test_process_task_skipped_when_no_source(tmp_path: Path) -> None:
    """Tasks without typed sources are honestly skipped, not guessed at."""
    _write_gt(
        tmp_path / "task",
        {
            "task_id": "t1",
            "producer_changed_files": ["foo.go"],  # raw breaking-change shape
        },
    )
    outcome = mod.process_task(tmp_path / "task")
    assert outcome.action == "skipped"


def test_process_task_dry_run_does_not_write(tmp_path: Path) -> None:
    gt_path = _write_gt(
        tmp_path / "task",
        {
            "task_id": "t1",
            "required_files": [
                {"path": "a.py", "repo": "demo", "source": "deterministic"}
            ],
        },
    )
    before = gt_path.read_text(encoding="utf-8")
    outcome = mod.process_task(tmp_path / "task", dry_run=True)
    assert outcome.action == "updated"
    assert gt_path.read_text(encoding="utf-8") == before


def test_process_task_reads_nested_ground_truth(tmp_path: Path) -> None:
    """Some EB families nest required_files under a ``ground_truth`` key."""
    _write_gt(
        tmp_path / "task",
        {
            "task_id": "t1",
            "ground_truth": {
                "tiers": ["deterministic"],
                "required_files": [
                    {"path": "a.py", "repo": "demo", "source": "curator"}
                ],
            },
        },
    )
    outcome = mod.process_task(tmp_path / "task")
    assert outcome.action == "updated"
    assert outcome.consensus == ("curator",)


# ---- run() walker -------------------------------------------------------


def test_run_excludes_archived(tmp_path: Path) -> None:
    """``_archived/`` subtrees are not mutated by the walker."""
    bench_root = tmp_path / "bench"
    bench_root.mkdir()
    _write_gt(
        bench_root / "active" / "t1",
        {
            "required_files": [
                {"path": "a.py", "repo": "demo", "source": "deterministic"}
            ]
        },
    )
    _write_gt(
        bench_root / "_archived" / "old",
        {
            "required_files": [
                {"path": "b.py", "repo": "demo", "source": "deterministic"}
            ]
        },
    )
    report = mod.run(bench_root)
    yielded_parts = {part for o in report.outcomes for part in o.task_dir.parts}
    assert "active" in yielded_parts
    assert "_archived" not in yielded_parts
