import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import render_findings

COMPARISON = {
    "scoring": "aligned_suffix_v1",
    "bootstrap_samples": 10,
    "bootstrap_seed": 1,
    "generation_seam": "claude_code_cli",
    "unscored_runs_excluded": {"total": 0, "by_condition_status": {}},
    "end_task_reward": "not measured at this layer",
    "conditions": {
        "lexical": {
            "n_runs": 2, "n_tasks": 2, "knows": "text",
            "completion": {"completion_rate": 1.0},
            "file_recall": 0.8, "file_precision": 0.4, "file_f1": 0.53,
            "within_task_recall_sd": None, "cost_usd": 0.4,
            "input_tokens": 100.0, "output_tokens": 10.0, "wall_seconds": 5.0,
            "tool_seconds": 1.0, "retrieval_result_chars": 900.0,
            "by_category": {"cross_file": {"n_runs": 2, "file_recall": 0.8}},
        }
    },
    "contrasts": {
        "lexical - structural": {"mean_difference": 0.1, "ci95": [0.02, 0.2], "n_tasks": 2,
         "excludes_zero": True}
    },
}
STATS = {
    "lexical": {
        "n_runs": 2, "first_primitive": {"lexical": 2}, "mean_total_calls": 3.0,
        "mean_first_relevant_call": 2.0, "runs_reaching_sufficient_evidence": 1,
        "mean_irrelevant_files_read": 0.5, "offered_but_never_used": {},
    }
}
TRACES = {"n_traces": 3, "most_divergent_task": "t1"}


def _render():
    return render_findings.render(COMPARISON, STATS, TRACES)


def test_the_four_claims_get_their_own_sections():
    text = _render()
    for heading in ("what agents PREFER", "what RETRIEVES the best evidence",
                    "what produces the best END-TASK outcome",
                    "what is CHEAPEST and FASTEST"):
        assert heading in text


def test_end_task_reward_is_stated_as_absent_not_substituted():
    assert "not measured at this layer" in _render()


def test_a_missing_number_renders_as_na_rather_than_zero():
    # within_task_recall_sd is None here; a 0.000 would claim the repeats
    # agreed perfectly.
    text = _render()
    assert "n/a" in text
    assert "0.000" not in text


def test_contrast_says_whether_the_interval_excludes_zero():
    assert "excludes 0" in _render()


def test_unused_tools_render_as_a_fraction_of_runs_offered():
    stats = {"lexical": {**STATS["lexical"],
                         "offered_but_never_used": {
                             "ast_search": {"runs_offered": 2, "runs_unused": 2}}}}
    assert "ast_search 2/2" in render_findings.render(COMPARISON, stats, TRACES)
