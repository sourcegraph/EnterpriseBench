import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import select_traces


def _summary(task, condition, recall, repeat=1, status="scored", calls=3):
    return {
        "task_id": task, "condition": condition, "repeat": repeat,
        "status": status, "file_recall": recall, "total_calls": calls,
        "first_primitive": "lexical", "primitive_phases": ["lexical"],
        "sequence": ["grep"], "queries": ["x"],
    }


def test_each_condition_contributes_a_best_and_a_worst():
    summaries = [
        _summary("t1", "lexical", 1.0),
        _summary("t2", "lexical", 0.0),
        _summary("t1", "structural", 0.5),
    ]
    reasons = [t["selected_because"] for t in select_traces.select(summaries)["traces"]]
    assert "highest recall in lexical" in reasons
    assert "lowest recall in lexical" in reasons


def test_divergent_task_is_the_widest_spread_not_the_lowest_score():
    summaries = [
        _summary("agree", "lexical", 0.1), _summary("agree", "structural", 0.1),
        _summary("split", "lexical", 1.0), _summary("split", "structural", 0.2),
    ]
    assert select_traces.divergent_task(summaries) == "split"


def test_single_condition_task_is_not_a_disagreement():
    assert select_traces.divergent_task([_summary("solo", "lexical", 0.9)]) is None


def test_unscored_runs_are_kept_rather_than_dropped():
    summaries = [
        _summary("t1", "lexical", 1.0),
        _summary("t2", "structural", None, status="invalid_turn_limit"),
    ]
    traces = select_traces.select(summaries)["traces"]
    assert any(t["selected_because"] == "unscored run: invalid_turn_limit"
               for t in traces)


def test_repeats_average_before_the_spread_is_taken():
    # Two structural repeats averaging 0.5 must not read as a 1.0 spread.
    summaries = [
        _summary("t", "lexical", 0.5),
        _summary("t", "structural", 1.0, repeat=1),
        _summary("t", "structural", 0.0, repeat=2),
        _summary("u", "lexical", 1.0), _summary("u", "structural", 0.0),
    ]
    assert select_traces.divergent_task(summaries) == "u"
