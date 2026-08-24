"""Offline tests for the tool-use analyzer.

These pin the behavioural claims the report will make, because each of them is
easy to compute subtly wrong in a way that flatters one condition: an
escalation that was really a single phase, a repeated query counted twice, a
"never used this tool" that was never offered it.
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import json
import analyze_traces
from analyze_traces import _normalized, aggregate, summarize_run


def _call(tool, hit_paths=(), **arguments):
    return {
        "tool": tool,
        "arguments": arguments,
        "seconds": 0.1,
        "result_chars": 100,
        "hit_paths": list(hit_paths),
    }


def _report(log, condition="combined", tools=None, required=("src/app/core.py",)):
    return {
        "task_id": "t1",
        "condition": condition,
        "repeat": 1,
        "condition_tools": tools
        or ["grep", "ast_search", "definition", "references", "read_file", "list_files", "submit_files"],
        "required_files": list(required),
        "tool_log": log,
        "ir_scores": {"file_recall": 1.0},
    }


def test_first_primitive_ignores_reads_and_listings():
    summary = summarize_run(
        _report([_call("list_files"), _call("read_file", path="a.py"), _call("grep", pattern="x")])
    )
    assert summary["first_tool"] == "list_files"
    assert summary["first_primitive"] == "lexical"


def test_consecutive_same_class_calls_are_one_phase():
    """Three greps are a lexical phase, not two lexical->lexical transitions."""
    summary = summarize_run(
        _report([_call("grep", pattern="a"), _call("grep", pattern="b"), _call("grep", pattern="c")])
    )
    assert summary["primitive_phases"] == ["lexical"]
    assert summary["transitions"] == {}


def test_escalation_records_the_class_moved_to():
    summary = summarize_run(
        _report(
            [
                _call("grep", pattern="Handler"),
                _call("read_file", path="a.py"),
                _call("ast_search", pattern="class $C"),
                _call("definition", symbol="Handler", path="a.py", repo="r"),
            ]
        )
    )
    assert summary["primitive_phases"] == ["lexical", "structural", "codeintel"]
    assert summary["escalated_from_lexical"] == "structural"
    assert summary["transitions"] == {"lexical->structural": 1, "structural->codeintel": 1}


def test_no_escalation_when_the_run_did_not_start_lexical():
    summary = summarize_run(
        _report([_call("ast_search", pattern="class $C"), _call("grep", pattern="x")])
    )
    assert summary["escalated_from_lexical"] is None


def test_metavariable_renames_count_as_the_same_query():
    assert _normalized("def $F($$$A)") == _normalized("def $NAME($$$ARGS)")
    summary = summarize_run(
        _report([_call("ast_search", pattern="def $F($$$A)"), _call("ast_search", pattern="def $NAME($$$ARGS)")])
    )
    assert summary["exact_repeat_queries"] == 0
    assert summary["near_repeat_queries"] == 1


def test_exact_repeat_queries_are_counted():
    summary = summarize_run(
        _report([_call("grep", pattern="Handler"), _call("grep", pattern="Handler")])
    )
    assert summary["exact_repeat_queries"] == 1


def test_unused_tools_are_only_the_offered_ones():
    summary = summarize_run(
        _report([_call("grep", pattern="x")], condition="lexical",
                tools=["grep", "read_file", "list_files", "submit_files"])
    )
    assert summary["offered_search_tools"] == ["grep"]
    assert summary["unused_search_tools"] == []

    summary = summarize_run(_report([_call("grep", pattern="x")]))
    assert summary["unused_search_tools"] == ["ast_search", "definition", "references"]


def test_evidence_indices_use_the_scoring_matcher():
    log = [
        _call("grep", pattern="x", hit_paths=["docs/readme.md"]),
        _call("grep", pattern="y", hit_paths=["app/core.py"]),  # suffix of the required path
    ]
    summary = summarize_run(_report(log))
    assert summary["first_relevant_call"] == 2
    assert summary["sufficient_evidence_call"] == 2


def test_sufficient_evidence_is_none_when_a_required_file_never_appears():
    summary = summarize_run(
        _report(
            [_call("grep", pattern="x", hit_paths=["src/app/core.py"])],
            required=("src/app/core.py", "src/app/other.py"),
        )
    )
    assert summary["first_relevant_call"] == 1
    assert summary["sufficient_evidence_call"] is None


def test_irrelevant_reads_are_counted_by_distinct_path():
    summary = summarize_run(
        _report(
            [
                _call("read_file", path="src/app/core.py"),
                _call("read_file", path="docs/a.md"),
                _call("read_file", path="docs/a.md"),
            ]
        )
    )
    assert summary["relevant_files_read"] == 1
    assert summary["irrelevant_files_read"] == 1


def test_aggregate_reports_offered_versus_unused():
    lexical_only = _report([_call("grep", pattern="x")], condition="lexical",
                           tools=["grep", "read_file", "list_files", "submit_files"])
    combined = _report([_call("grep", pattern="x")])
    stats = aggregate([summarize_run(lexical_only), summarize_run(combined)])
    assert stats["lexical"]["offered_but_never_used"] == {}
    unused = stats["combined"]["offered_but_never_used"]
    assert unused["ast_search"] == {"runs_offered": 1, "runs_unused": 1}
    assert stats["combined"]["first_primitive"] == {"lexical": 1}


def test_analysis_output_in_the_reports_tree_is_not_loaded_as_a_run(tmp_path):
    """comparison.json has no `condition`; loading it invents a null arm."""
    (tmp_path / "comparison.json").write_text(
        json.dumps({"conditions": {}, "contrasts": {}})
    )
    (tmp_path / "run.json").write_text(
        json.dumps({"condition": "lexical", "task_id": "t1", "tool_log": []})
    )
    loaded = analyze_traces.load_reports(tmp_path)
    assert [r["task_id"] for r in loaded] == ["t1"]
