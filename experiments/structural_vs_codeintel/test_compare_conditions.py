"""Tests for the statistics, which are the part easiest to get quietly wrong."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import compare_conditions
from compare_conditions import _per_task, _within_task_sd, contrasts, paired_bootstrap


def _report(condition, task_id, recall, repeat=1):
    return {
        "condition": condition,
        "task_id": task_id,
        "repeat": repeat,
        "condition_knows": "text",
        "scoring": "aligned_suffix_v1",
        "ir_scores": {"file_recall": recall, "file_precision": recall, "file_f1": recall},
    }


def test_repeats_are_averaged_before_the_task_enters_the_sample():
    """Otherwise three repeats of one task would count as three tasks."""
    reports = [
        _report("lexical", "t1", 0.0, repeat=1),
        _report("lexical", "t1", 1.0, repeat=2),
    ]
    assert _per_task(reports, "file_recall")["lexical"] == {"t1": 0.5}


def test_paired_bootstrap_finds_a_consistent_difference():
    left = {f"t{i}": 0.8 for i in range(12)}
    right = {f"t{i}": 0.4 for i in range(12)}
    result = paired_bootstrap(left, right, samples=2000)
    assert result["mean_difference"] == 0.4
    assert result["excludes_zero"]
    assert result["n_tasks"] == 12


def test_paired_bootstrap_does_not_claim_a_difference_from_noise():
    left = {f"t{i}": (0.9 if i % 2 else 0.1) for i in range(12)}
    right = {f"t{i}": (0.1 if i % 2 else 0.9) for i in range(12)}
    result = paired_bootstrap(left, right, samples=2000)
    assert abs(result["mean_difference"]) < 0.01
    assert not result["excludes_zero"]


def test_paired_bootstrap_needs_two_shared_tasks():
    assert paired_bootstrap({"t1": 1.0}, {"t2": 0.0}) is None


def test_pairing_uses_only_tasks_both_conditions_ran():
    left = {"t1": 1.0, "t2": 1.0, "t3": 1.0}
    right = {"t1": 0.5, "t2": 0.5}
    assert paired_bootstrap(left, right, samples=500)["n_tasks"] == 2


def test_within_task_sd_measures_run_to_run_spread():
    reports = [_report("lexical", "t1", 0.0), _report("lexical", "t1", 1.0)]
    assert _within_task_sd(reports) == round(0.7071067811865476, 4)
    assert _within_task_sd([_report("lexical", "t1", 0.5)]) is None


def test_contrasts_are_scoped_per_category():
    """An overall wash can hide two real, opposite per-category effects."""
    reports = []
    for task in ("t1", "t2", "t3"):
        reports += [_report("lexical", task, 1.0), _report("structural", task, 0.0)]
    for task in ("t4", "t5", "t6"):
        reports += [_report("lexical", task, 0.0), _report("structural", task, 1.0)]
    categories = {
        "t1": ["lexical_exactness"],
        "t2": ["lexical_exactness"],
        "t3": ["lexical_exactness"],
        "t4": ["syntactic_pattern"],
        "t5": ["syntactic_pattern"],
        "t6": ["syntactic_pattern"],
    }
    result = contrasts(reports, categories)["lexical - structural"]
    assert result["mean_difference"] == 0.0  # the two effects cancel overall
    assert result["by_category"]["lexical_exactness"]["mean_difference"] == 1.0
    assert result["by_category"]["syntactic_pattern"]["mean_difference"] == -1.0


def test_a_category_with_one_task_reports_no_interval_rather_than_a_fake_one():
    reports = [_report("lexical", "t1", 1.0), _report("structural", "t1", 0.0),
               _report("lexical", "t2", 1.0), _report("structural", "t2", 0.0)]
    result = contrasts(reports, {"t1": ["solo"], "t2": ["other"]})["lexical - structural"]
    assert result["by_category"] == {}


def test_a_two_task_stratum_keeps_its_estimate_and_loses_its_interval():
    """A paired bootstrap over two tasks has three distinct resamples; its
    interval can exclude zero on arithmetic alone, so only the mean survives."""
    left = {"t1": 1.0, "t2": 1.0, "t3": 0.5}
    right = {"t1": 0.0, "t2": 0.0, "t3": 0.5}
    cell = compare_conditions._scoped(left, right, {"t1", "t2"})
    assert cell == {"n_tasks": 2, "underpowered": True, "mean_difference": 1.0}
    assert "ci95" not in cell and "excludes_zero" not in cell


def test_an_empty_stratum_reports_its_size_and_no_estimate():
    cell = compare_conditions._scoped({"t1": 1.0}, {"t1": 0.0}, {"absent"})
    assert cell == {"n_tasks": 0, "underpowered": True}


def test_contrasts_are_stratified_by_index_tier():
    """The tier the code-intel arm actually had is a separate cut from category:
    a whole-corpus win can live entirely in the stratum with the weakest index."""
    reports = []
    for task in ("t1", "t2", "t3"):
        reports += [_report("codeintel", task, 0.5), _report("lexical", task, 0.5)]
    for task in ("t4", "t5", "t6"):
        reports += [_report("codeintel", task, 1.0), _report("lexical", task, 0.0)]
    tiers = dict.fromkeys(("t1", "t2", "t3"), "semantic")
    tiers.update(dict.fromkeys(("t4", "t5", "t6"), "syntactic"))
    result = contrasts(reports, {}, tiers)["codeintel - lexical"]
    assert result["mean_difference"] == 0.5
    assert result["by_index_tier"]["semantic"]["mean_difference"] == 0.0
    assert result["by_index_tier"]["syntactic"]["mean_difference"] == 1.0


def test_tier_stratification_is_absent_when_no_tier_map_was_recorded():
    """index_tier.json is optional; its absence must not fabricate a tier column."""
    reports = [_report("codeintel", "t1", 1.0), _report("lexical", "t1", 0.0),
               _report("codeintel", "t2", 1.0), _report("lexical", "t2", 0.0)]
    result = contrasts(reports, {})["codeintel - lexical"]
    assert "by_index_tier" not in result


def test_unknown_tier_tasks_are_dropped_rather_than_binned(tmp_path: Path):
    """An unasked instance is an absent measurement, not a fourth tier."""
    manifest = tmp_path / "index_tier.json"
    manifest.write_text(
        '{"tasks": {"t1": "semantic", "t2": "unknown", "t3": "syntactic"}}'
    )
    assert compare_conditions.load_task_tiers(manifest) == {
        "t1": "semantic",
        "t3": "syntactic",
    }


def test_missing_tier_manifest_is_not_an_error(tmp_path: Path):
    assert compare_conditions.load_task_tiers(tmp_path / "nope.json") == {}


def test_file_precision_and_f1_are_derived_from_the_scorer_s_real_keys():
    """The scorer reports precision/recall/f1 as @k dicts and only file_recall
    as a scalar; naming file_precision directly would blank the column."""
    reports = [
        {
            "condition": "lexical",
            "task_id": "t1",
            "ir_scores": {
                "file_recall": 1.0,
                "n_overlap": 3,
                "n_retrieved": 6,
                "n_ground_truth": 3,
                "precision": {5: 0.6},
                "f1": {5: 0.75},
            },
        }
    ]
    compare_conditions.derive_file_metrics(reports)
    scores = reports[0]["ir_scores"]
    assert scores["file_precision"] == 0.5
    assert scores["file_f1"] == 0.6667


def test_a_run_that_submitted_nothing_has_no_precision():
    """0.0 would read as a measured miss; absence is not a score."""
    reports = [
        {
            "condition": "lexical",
            "task_id": "t1",
            "ir_scores": {"file_recall": 0.0, "n_overlap": 0, "n_retrieved": 0},
        }
    ]
    compare_conditions.derive_file_metrics(reports)
    assert reports[0]["ir_scores"]["file_precision"] is None
    assert reports[0]["ir_scores"]["file_f1"] is None


def test_completion_rate_is_reported_per_condition():
    """Means are conditional on completion; an arm that ran out of budget on
    half its tasks must not present its survivors as its record."""
    scored = [
        {"condition": "structural", "task_id": "t1", "ir_scores": {"file_recall": 1.0}},
    ]
    unscored = [
        {"condition": "structural", "task_id": "t2", "status": "invalid_turn_limit"},
        {"condition": "structural", "task_id": "t3", "status": "invalid_turn_limit"},
        {"condition": "lexical", "task_id": "t1", "status": "invalid_cli_error"},
    ]
    rows = compare_conditions.completion(scored, unscored)
    assert rows["structural"]["attempted"] == 3
    assert rows["structural"]["completion_rate"] == round(1 / 3, 4)
    assert rows["structural"]["excluded_by_status"] == {"invalid_turn_limit": 2}
    assert rows["lexical"]["completion_rate"] == 0.0


def test_condition_rows_declare_that_metrics_are_conditional():
    reports = [
        {
            "condition": "lexical",
            "task_id": "t1",
            "ir_scores": {"file_recall": 1.0, "n_overlap": 1, "n_retrieved": 1},
        }
    ]
    compare_conditions.derive_file_metrics(reports)
    rows = compare_conditions.by_condition(
        reports, {}, compare_conditions.completion(reports, [])
    )
    assert "conditional" in rows["lexical"]["metrics_are"]
    assert rows["lexical"]["completion"]["completion_rate"] == 1.0


def test_dollar_cost_is_reported_per_condition():
    # Efficiency is one of the four separate claims; a condition row that
    # reported only tokens would leave price to be re-derived by hand.
    reports = [
        {"condition": "lexical", "task_id": "t1", "cost_usd": 0.40,
         "ir_scores": {"file_recall": 1.0, "n_overlap": 1, "n_retrieved": 1,
                       "n_ground_truth": 1}},
        {"condition": "lexical", "task_id": "t2", "cost_usd": 0.60,
         "ir_scores": {"file_recall": 1.0, "n_overlap": 1, "n_retrieved": 1,
                       "n_ground_truth": 1}},
    ]
    compare_conditions.derive_file_metrics(reports)
    rows = compare_conditions.by_condition(reports, {}, {})
    assert rows["lexical"]["cost_usd"] == 0.5
