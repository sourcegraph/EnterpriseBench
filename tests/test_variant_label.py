"""Variant-label threading through run identity (EnterpriseBench-zsozk.1).

A labeled run is a prompt/preamble tuning iteration. The label must enter run
identity end to end — output path, results.json, cost attribution, analysis
dedup — or two variants of the same (task_id, mode) overwrite on disk and
silently collapse in every aggregate. Pattern source: sourcegraph PR 13940's
``comparison_variant`` (raw mode stays machine-meaningful; the label carries
human grouping), hardened here to also partition storage and scoring.

The other invariant pinned here is the opposite one: with no label set, every
path, key, and payload is byte-identical to pre-label behavior.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_ROOT / "scripts"))
sys.path.insert(0, str(_ROOT / "scripts" / "orchestration"))

from lib.shared import split_variant_label  # noqa: E402

import cost_tracker  # noqa: E402
from cost_tracker import (  # noqa: E402
    TaskCost,
    Usage,
    _parse_dir_identity,
    aggregate_report,
    comparison_attempts,
)
from analyze_scores import analyze, load_all_results, parse_result  # noqa: E402
from run_benchmark import _mode_output_dir, collect_passthrough_args  # noqa: E402
from run_sweep import _check_one_item  # noqa: E402
from runner_cli import PASSTHROUGH_FLAGS  # noqa: E402
from run_task import (  # noqa: E402
    TaskRunConfig,
    TaskRunResult,
    _resolve_output_dir,
    _save_results,
    _validate_variant_label,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _usage(**overrides) -> Usage:
    defaults = dict(
        input_tokens=1000,
        output_tokens=200,
        cache_write_tokens=0,
        cache_read_tokens=0,
        model="claude-sonnet-5",
        num_requests=3,
    )
    defaults.update(overrides)
    return Usage(**defaults)


def _task_cost(
    task_id: str = "task-a",
    mode: str = "mcp_only",
    variant_label: str | None = None,
    normalized_score: float | None = 0.5,
    run_dir: str = "results/runs/task-a/mcp_only",
) -> TaskCost:
    return TaskCost(
        task_id=task_id,
        mode=mode,
        suite="customer_escalation",
        difficulty="medium",
        usage=_usage(),
        trace_cost_usd=0.01,
        vendor=None,
        agent_duration_seconds=10.0,
        run_dir=run_dir,
        normalized_score=normalized_score,
        variant_label=variant_label,
    )


def _make_config(**overrides) -> TaskRunConfig:
    defaults = dict(
        task_toml=Path("/fake/task.toml"),
        agent_command="claude -p",
        mode="mcp_only",
    )
    defaults.update(overrides)
    return TaskRunConfig(**defaults)


def _make_run_result(task_id: str = "task-a") -> TaskRunResult:
    return TaskRunResult(
        task_id=task_id,
        phase="complete",
        success=True,
        error="",
        image_tag=f"eb-{task_id}",
        container_id="abc123",
        scores={
            "task_score": 1.0,
            "all_passed": True,
            "checkpoints_passed": 1,
            "checkpoints_total": 1,
        },
        timing={"agent": 1.0},
        output_dir="",
        tool_usage={},
    )


def _make_task_data(task_id: str = "task-a") -> dict:
    return {
        "task": {
            "id": task_id,
            "suite": "customer_escalation",
            "task_type": "error_provenance",
            "difficulty": "medium",
            "session_type": "single",
        },
        "metadata": {"languages": ["python"]},
    }


def _write_results_json(
    path: Path,
    task_id: str = "task-a",
    mode: str = "mcp_only",
    variant_label: str | None = None,
    task_score: float = 1.0,
) -> Path:
    config: dict = {"mode": mode}
    if variant_label is not None:
        config["variant_label"] = variant_label
    payload = {
        "task_id": task_id,
        "success": True,
        "scores": {
            "task_score": task_score,
            "score_contract_version": 2,
            "all_passed": True,
            "checkpoints_passed": 1,
            "checkpoints_total": 2,
        },
        "config": config,
        "task_metadata": {
            "suite": "customer_escalation",
            "task_type": "error_provenance",
            "difficulty": "medium",
            "languages": ["python"],
        },
    }
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload))
    return path


# ---------------------------------------------------------------------------
# shared.split_variant_label
# ---------------------------------------------------------------------------


class TestSplitVariantLabel:
    def test_labeled_dirname(self):
        assert split_variant_label("mcp_only--p3") == ("mcp_only", "p3")

    def test_unlabeled_dirname(self):
        assert split_variant_label("baseline") == ("baseline", None)

    def test_mode_with_underscore(self):
        assert split_variant_label("mcp_only") == ("mcp_only", None)

    def test_label_with_hyphen(self):
        assert split_variant_label("hybrid--finder-short2") == (
            "hybrid",
            "finder-short2",
        )

    def test_splits_on_first_separator_only(self):
        # The validator forbids "--" inside labels, so a first-split is total.
        assert split_variant_label("cli--a--b") == ("cli", "a--b")


# ---------------------------------------------------------------------------
# run_task: label validation
# ---------------------------------------------------------------------------


class TestValidateVariantLabel:
    @pytest.mark.parametrize("label", ["p3", "finder-short2", "a", "0-9"])
    def test_accepts_valid(self, label: str):
        assert _validate_variant_label(label) == label

    @pytest.mark.parametrize(
        "label",
        ["", "A3", "a_b", "a--b", "-a", "a-", "p3 ", "p.3", "é"],
    )
    def test_rejects_invalid(self, label: str):
        with pytest.raises(ValueError):
            _validate_variant_label(label)


# ---------------------------------------------------------------------------
# run_task: output dir resolution
# ---------------------------------------------------------------------------


class TestResolveOutputDir:
    def test_unlabeled_path_unchanged(self):
        config = _make_config(mode="mcp_only")
        out = _resolve_output_dir(config, "task-a")
        assert out == cost_tracker.PROJECT_ROOT / "results" / "runs" / "task-a" / "mcp_only"

    def test_unlabeled_rep_path_unchanged(self):
        config = _make_config(mode="mcp_only", rep=2)
        out = _resolve_output_dir(config, "task-a")
        assert out.parts[-3:] == ("task-a", "mcp_only", "rep2")

    def test_labeled_path_gets_segment(self):
        config = _make_config(mode="mcp_only", variant_label="p3")
        out = _resolve_output_dir(config, "task-a")
        assert out.parts[-2:] == ("task-a", "mcp_only--p3")

    def test_labeled_rep_nests_under_label(self):
        config = _make_config(mode="mcp_only", variant_label="p3", rep=1)
        out = _resolve_output_dir(config, "task-a")
        assert out.parts[-3:] == ("task-a", "mcp_only--p3", "rep1")

    def test_explicit_output_dir_wins(self, tmp_path: Path):
        config = _make_config(output_dir=tmp_path, variant_label="p3")
        assert _resolve_output_dir(config, "task-a") == tmp_path


# ---------------------------------------------------------------------------
# run_task: persistence
# ---------------------------------------------------------------------------


class TestSaveResultsVariantLabel:
    def test_label_written_to_results_and_config(self, tmp_path: Path):
        config = _make_config(variant_label="p3")
        _save_results(_make_run_result(), _make_task_data(), tmp_path, config)

        results = json.loads((tmp_path / "results.json").read_text())
        assert results["config"]["variant_label"] == "p3"
        config_json = json.loads((tmp_path / "config.json").read_text())
        assert config_json["variant_label"] == "p3"

    def test_unlabeled_payloads_have_no_label_key(self, tmp_path: Path):
        """Byte-compat: an unset label adds no key to either artifact."""
        config = _make_config()
        _save_results(_make_run_result(), _make_task_data(), tmp_path, config)

        results = json.loads((tmp_path / "results.json").read_text())
        assert "variant_label" not in results["config"]
        config_json = json.loads((tmp_path / "config.json").read_text())
        assert "variant_label" not in config_json


# ---------------------------------------------------------------------------
# cost_tracker: directory identity
# ---------------------------------------------------------------------------


class TestParseDirIdentity:
    def test_labeled_multi_mode_dir(self, tmp_path: Path):
        d = tmp_path / "runs" / "task-a" / "mcp_only--p3"
        assert _parse_dir_identity(d) == ("task-a", "mcp_only", "p3")

    def test_unlabeled_multi_mode_dir(self, tmp_path: Path):
        d = tmp_path / "runs" / "task-a" / "mcp_only"
        assert _parse_dir_identity(d) == ("task-a", "mcp_only", None)

    def test_legacy_single_mode_dir(self, tmp_path: Path):
        d = tmp_path / "runs" / "task-a"
        assert _parse_dir_identity(d) == ("task-a", "baseline", None)

    def test_mcp_batch_suffix_dir(self, tmp_path: Path):
        d = tmp_path / "mcp_batch1" / "task-a_hybrid"
        assert _parse_dir_identity(d) == ("task-a", "hybrid", None)


# ---------------------------------------------------------------------------
# cost_tracker: comparison exclusion + disclosure
# ---------------------------------------------------------------------------


class TestCostComparisonExcludesLabeled:
    def test_labeled_attempt_never_enters_comparison(self):
        costs = [
            _task_cost(mode="baseline", run_dir="results/runs/task-a/baseline"),
            _task_cost(mode="mcp_only", run_dir="results/runs/task-a/mcp_only"),
            _task_cost(
                mode="mcp_only",
                variant_label="p3",
                normalized_score=0.9,
                run_dir="results/runs/task-a/mcp_only--p3",
            ),
        ]
        comparison = comparison_attempts(costs)
        # The labeled attempt scores higher; without the exclusion it would win
        # the (task-a, mcp_only) cell and contaminate the arm comparison.
        assert all(tc.variant_label is None for tc in comparison.rows)
        assert len(comparison.rows) == 2

    def test_labeled_attempts_do_not_define_arms(self):
        # A label-only "arm" must not enter the mode intersection: task-a ran
        # baseline+mcp_only unlabeled; the labeled hybrid attempt must not
        # force hybrid into the arm set and empty the matched intersection.
        costs = [
            _task_cost(mode="baseline", run_dir="r/a-b"),
            _task_cost(mode="mcp_only", run_dir="r/a-m"),
            _task_cost(mode="hybrid", variant_label="p3", run_dir="r/a-h"),
        ]
        comparison = comparison_attempts(costs)
        assert list(comparison.modes) == ["baseline", "mcp_only"]
        assert comparison.task_ids == ("task-a",)

    def test_report_discloses_excluded_labeled_attempts(self):
        costs = [
            _task_cost(mode="baseline", run_dir="r/b"),
            _task_cost(mode="mcp_only", run_dir="r/m"),
            _task_cost(mode="mcp_only", variant_label="p3", run_dir="r/m-p3"),
        ]
        report = aggregate_report(costs)
        excluded = report["comparison_economics"]["excluded_variant_labeled_attempts"]
        assert excluded == [
            {
                "task_id": "task-a",
                "mode": "mcp_only",
                "variant_label": "p3",
                "run_dir": "r/m-p3",
                "cost_usd": 0.01,
            }
        ]

    def test_labeled_attempts_stay_in_operational_view(self):
        costs = [
            _task_cost(mode="mcp_only", run_dir="r/m"),
            _task_cost(mode="mcp_only", variant_label="p3", run_dir="r/m-p3"),
        ]
        report = aggregate_report(costs)
        assert report["operational_economics"]["attempts"] == 2

    def test_attempt_row_carries_label(self):
        report = aggregate_report(
            [_task_cost(mode="mcp_only", variant_label="p3", run_dir="r/m-p3")]
        )
        assert report["per_attempt"][0]["variant_label"] == "p3"

    def test_unlabeled_report_has_no_disclosure_rows(self):
        report = aggregate_report([_task_cost(run_dir="r/m")])
        assert report["comparison_economics"]["excluded_variant_labeled_attempts"] == []


# ---------------------------------------------------------------------------
# run_benchmark: routing + passthrough
# ---------------------------------------------------------------------------


class TestBenchmarkRouting:
    def test_mode_output_dir_labeled(self):
        out = _mode_output_dir("task-a", "mcp_only", multi_mode=True, variant_label="p3")
        assert out is not None
        assert out.parts[-2:] == ("task-a", "mcp_only--p3")

    def test_mode_output_dir_unlabeled_unchanged(self):
        out = _mode_output_dir("task-a", "mcp_only", multi_mode=True)
        assert out is not None
        assert out.parts[-2:] == ("task-a", "mcp_only")

    def test_variant_label_in_passthrough_contract(self):
        assert "--variant-label" in PASSTHROUGH_FLAGS

    def test_collect_passthrough_emits_label(self):
        import argparse

        args = argparse.Namespace(
            source=None,
            agent=None,
            timeout=None,
            account=None,
            mode="mcp_only",
            dry_run=False,
            variant_label="p3",
        )
        flags = collect_passthrough_args(args)
        assert "--variant-label" in flags
        assert flags[flags.index("--variant-label") + 1] == "p3"

    def test_collect_passthrough_omits_unset_label(self):
        import argparse

        args = argparse.Namespace(
            source=None,
            agent=None,
            timeout=None,
            account=None,
            mode="mcp_only",
            dry_run=False,
            variant_label=None,
        )
        assert "--variant-label" not in collect_passthrough_args(args)


# ---------------------------------------------------------------------------
# run_sweep: labeled runs never satisfy sweep completion
# ---------------------------------------------------------------------------


class TestSweepCompletionIsolation:
    def test_labeled_result_does_not_complete_unlabeled_item(self, tmp_path: Path):
        _write_results_json(
            tmp_path / "task-a" / "mcp_only--p3" / "results.json",
            variant_label="p3",
        )
        status, path = _check_one_item("task-a", "mcp_only", [tmp_path])
        assert status == "pending"
        assert path is None

    def test_unlabeled_result_still_completes(self, tmp_path: Path):
        _write_results_json(tmp_path / "task-a" / "mcp_only" / "results.json")
        status, _ = _check_one_item("task-a", "mcp_only", [tmp_path])
        assert status == "scored"


# ---------------------------------------------------------------------------
# analyze_scores: labeled rows are quarantined from headline analysis
# ---------------------------------------------------------------------------


class TestAnalyzeScoresLabel:
    def test_parse_result_reads_label(self, tmp_path: Path):
        p = _write_results_json(
            tmp_path / "runs" / "task-a" / "mcp_only--p3" / "results.json",
            variant_label="p3",
        )
        tr = parse_result(p, tmp_path / "benchmarks")
        assert tr is not None
        assert tr.variant_label == "p3"
        assert tr.mode == "mcp_only"

    def test_parse_result_unlabeled_is_none(self, tmp_path: Path):
        p = _write_results_json(
            tmp_path / "runs" / "task-a" / "mcp_only" / "results.json"
        )
        tr = parse_result(p, tmp_path / "benchmarks")
        assert tr is not None
        assert tr.variant_label is None

    def test_load_all_results_excludes_labeled_by_default(self, tmp_path: Path):
        runs = tmp_path / "runs"
        _write_results_json(runs / "task-a" / "mcp_only" / "results.json")
        _write_results_json(
            runs / "task-a" / "mcp_only--p3" / "results.json",
            variant_label="p3",
            task_score=2.0,
        )
        results = load_all_results([runs], tmp_path / "benchmarks")
        assert len(results) == 1
        assert results[0].variant_label is None

    def test_load_all_results_can_include_labeled(self, tmp_path: Path):
        runs = tmp_path / "runs"
        _write_results_json(runs / "task-a" / "mcp_only" / "results.json")
        _write_results_json(
            runs / "task-a" / "mcp_only--p3" / "results.json",
            variant_label="p3",
            task_score=2.0,
        )
        results = load_all_results(
            [runs], tmp_path / "benchmarks", include_variant_labeled=True
        )
        # Dedup key is (task_id, mode, label): both rows survive; the higher-
        # scoring labeled row must NOT displace the unlabeled one.
        assert len(results) == 2
        labels = {r.variant_label for r in results}
        assert labels == {None, "p3"}

    def test_excluded_labeled_result_does_not_block_headline_contract_check(
        self, tmp_path: Path
    ):
        runs = tmp_path / "runs"
        unlabeled = _write_results_json(
            runs / "task-a" / "mcp_only" / "results.json"
        )
        labeled = _write_results_json(
            runs / "task-a" / "mcp_only--p3" / "results.json",
            variant_label="p3",
        )
        data = json.loads(labeled.read_text())
        data["scores"].pop("score_contract_version")
        labeled.write_text(json.dumps(data))

        results = load_all_results([runs], tmp_path / "benchmarks")

        assert [result.source_path for result in results] == [str(unlabeled)]

    def test_analysis_reports_variants_without_mixing_headline_modes(
        self, tmp_path: Path
    ):
        runs = tmp_path / "runs"
        _write_results_json(
            runs / "task-a" / "baseline" / "results.json",
            mode="baseline",
            task_score=0.25,
        )
        _write_results_json(
            runs / "task-a" / "baseline--codex" / "results.json",
            mode="baseline",
            variant_label="codex",
            task_score=0.75,
        )

        report = analyze(
            [runs],
            tmp_path / "benchmarks",
            include_variant_labeled=True,
        )

        assert report["by_mode"]["baseline"]["mean"] == 0.25
        assert report["by_variant"]["codex"]["mean"] == 0.75
