"""Tests for the shared results.json contract (scripts/orchestration/results_io.py).

The point of this module is the reader-contract class below. results_io only
matters if what it writes is what the real readers read, so those tests call the
actual reader functions (``run_benchmark.is_task_completed``,
``run_benchmark.extract_task_cost``, ``run_sweep._check_results_file``) against a
file the helper produced, rather than re-asserting the helper's own dict back at
itself. A key dropped from the payload fails here instead of surfacing as a task
that silently re-runs or a cost that silently reads 0.0 (EnterpriseBench-78zt5).
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))
sys.path.insert(
    0, str(Path(__file__).resolve().parent.parent / "scripts" / "orchestration")
)

from results_io import (  # noqa: E402
    PHASE_COMPLETE,
    RESULTS_FIELDS,
    RESULTS_FILENAME,
    RUN_STATUS_INVALID,
    write_results_json,
)
from run_benchmark import extract_task_cost, is_task_completed  # noqa: E402
from run_sweep import _check_results_file  # noqa: E402


def _write(output_dir: Path, **fields) -> dict:
    """Write a results.json and return what a reader opening it would see."""
    return json.loads(write_results_json(output_dir, **fields).read_text())


# ---------------------------------------------------------------------------
# Shape and file mechanics
# ---------------------------------------------------------------------------


class TestWriteResultsJson:
    def test_payload_carries_every_declared_field(self, tmp_path: Path) -> None:
        assert tuple(_write(tmp_path, task_id="t-1", success=True)) == RESULTS_FIELDS

    def test_absent_dicts_are_empty_not_missing(self, tmp_path: Path) -> None:
        """A reader must never distinguish "runner omitted it" from "no value"."""
        payload = _write(tmp_path, task_id="t-1", success=False)
        for field in ("scores", "timing", "tool_usage", "config", "task_metadata"):
            assert payload[field] == {}, f"{field} should default to an empty dict"

    def test_task_id_and_success_are_required(self, tmp_path: Path) -> None:
        with pytest.raises(TypeError):
            write_results_json(tmp_path, success=True)  # type: ignore[call-arg]
        with pytest.raises(TypeError):
            write_results_json(tmp_path, task_id="t-1")  # type: ignore[call-arg]

    def test_misspelled_field_is_rejected(self, tmp_path: Path) -> None:
        """A typo must raise, not write a key no reader reads."""
        with pytest.raises(TypeError):
            write_results_json(tmp_path, task_id="t-1", success=True, scoress={})  # type: ignore[call-arg]
        assert not (tmp_path / RESULTS_FILENAME).exists()

    def test_values_are_passed_through(self, tmp_path: Path) -> None:
        payload = _write(
            tmp_path,
            task_id="t-1",
            success=False,
            phase="build_failed",
            status=RUN_STATUS_INVALID,
            error="boom",
            failure_class="infra_build",
            image_tag="eb-t-1",
            scores={"task_score": 1.5},
            timing={"agent": 2.0},
            tool_usage={"cost_usd": 0.25},
            config={"mode": "hybrid"},
            task_metadata={"suite": "s"},
        )
        assert payload["success"] is False
        assert payload["status"] == RUN_STATUS_INVALID
        assert payload["failure_class"] == "infra_build"
        assert payload["scores"]["task_score"] == pytest.approx(1.5)
        assert payload["tool_usage"]["cost_usd"] == pytest.approx(0.25)

    def test_writes_named_file_and_returns_path(self, tmp_path: Path) -> None:
        path = write_results_json(tmp_path / "out", task_id="t-1", success=True)
        assert path == tmp_path / "out" / RESULTS_FILENAME
        assert path.exists()

    def test_creates_missing_parents(self, tmp_path: Path) -> None:
        path = write_results_json(tmp_path / "a" / "b" / "c", task_id="t-1", success=True)
        assert path.exists()

    def test_output_is_valid_json_with_trailing_newline(self, tmp_path: Path) -> None:
        path = write_results_json(tmp_path, task_id="t-1", success=True)
        text = path.read_text()
        assert text.endswith("\n")
        assert json.loads(text)["task_id"] == "t-1"

    def test_rewrite_replaces_rather_than_appends(self, tmp_path: Path) -> None:
        write_results_json(tmp_path, task_id="t-1", success=False)
        assert _write(tmp_path, task_id="t-1", success=True)["success"] is True


# ---------------------------------------------------------------------------
# Reader contract — helper output vs the real readers
# ---------------------------------------------------------------------------


def _write_for_reader(results_dir: Path, task_id: str, mode: str, **fields) -> Path:
    """Write a results.json where run_benchmark's readers actually look."""
    return write_results_json(results_dir / task_id / mode, task_id=task_id, **fields)


class TestReaderContract:
    """Bind helper output to each real reader. These fail if the shape drifts."""

    def test_is_task_completed_sees_a_successful_run(self, tmp_path: Path) -> None:
        _write_for_reader(tmp_path, "t-1", "baseline", success=True)
        assert is_task_completed("t-1", results_dir=tmp_path, mode="baseline") is True

    def test_is_task_completed_rejects_an_unsuccessful_run(self, tmp_path: Path) -> None:
        _write_for_reader(tmp_path, "t-1", "baseline", success=False)
        assert is_task_completed("t-1", results_dir=tmp_path, mode="baseline") is False

    def test_extract_task_cost_sees_the_cost(self, tmp_path: Path) -> None:
        _write_for_reader(
            tmp_path, "t-1", "baseline", success=True, tool_usage={"cost_usd": 0.42}
        )
        cost = extract_task_cost("t-1", results_dir=tmp_path, mode="baseline")
        assert cost == pytest.approx(0.42)

    def test_extract_task_cost_reads_empty_tool_usage_as_zero(self, tmp_path: Path) -> None:
        """A runner with no cost accounting must not crash the rollup."""
        _write_for_reader(tmp_path, "t-1", "baseline", success=True, tool_usage={})
        assert extract_task_cost("t-1", results_dir=tmp_path, mode="baseline") == 0.0

    def test_sweep_reader_calls_a_successful_run_scored(self, tmp_path: Path) -> None:
        path = _write_for_reader(tmp_path, "t-1", "baseline", success=True)
        assert _check_results_file(path) == "scored"

    def test_sweep_reader_calls_an_unsuccessful_run_failed(self, tmp_path: Path) -> None:
        path = _write_for_reader(tmp_path, "t-1", "baseline", success=False)
        assert _check_results_file(path) == "failed"

    def test_score_reader_expression_finds_task_score(self, tmp_path: Path) -> None:
        """Mirrors run_benchmark.run_task's inline read (run_benchmark.py:354).

        It is an expression rather than a function, so this is the one reader
        that cannot be called directly; the read is reproduced verbatim.
        """
        path = _write_for_reader(
            tmp_path, "t-1", "baseline", success=True, scores={"task_score": 2.5}
        )
        rdata = json.loads(path.read_text())
        score = rdata.get("scores", {}).get("task_score", rdata.get("score"))
        assert score == pytest.approx(2.5)

    def test_headline_filter_accepts_a_scored_run(self, tmp_path: Path) -> None:
        """analysis/recompute_headline_* gate on phase == complete AND success."""
        path = _write_for_reader(
            tmp_path, "t-1", "baseline", success=True, phase="complete"
        )
        d = json.loads(path.read_text())
        assert d.get("phase") == "complete" and d.get("success") is True

    def test_invalid_status_is_readable_by_the_aggregator(self, tmp_path: Path) -> None:
        """analysis/aggregate_mcp_clean drops rows whose status is "invalid"."""
        path = _write_for_reader(
            tmp_path, "t-1", "baseline", success=False, status=RUN_STATUS_INVALID
        )
        assert json.loads(path.read_text())["status"] == "invalid"

    def test_every_reader_field_is_declared_in_the_contract(self) -> None:
        """Drift guard: the fields the readers above touch are all top-level keys."""
        reader_fields = {"success", "scores", "tool_usage", "phase", "status"}
        assert reader_fields <= set(RESULTS_FIELDS)

    def test_contract_values_match_the_literals_readers_compare_against(self) -> None:
        """The VALUES are contract, not just the keys.

        Every reader of these two fields hardcodes the string
        (``d.get("phase") == "complete"`` in scripts/analysis/recompute_headline_*,
        ``!= "invalid"`` in the mcp aggregator), so a runner importing the constant
        is only safe while the constant still holds the string they expect.
        Asserted against literals on purpose: importing the constant into the
        assertion would make this pass for any value and guard nothing.
        """
        assert PHASE_COMPLETE == "complete"
        assert RUN_STATUS_INVALID == "invalid"
