import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent))

import analyze_traces
import conditions
from conditions import (
    ABLATION_CONTROLS,
    ABLATION_TOOLS,
    AST_SEARCH_TOOL,
    AST_SEARCH_TOOL_WITH_EXAMPLE,
    CONDITIONS,
    PILOT_CONDITIONS,
    RETRIEVAL_TOOLS,
    check_ablation_declarations,
    check_description_parity,
)


def _pattern_doc(tool):
    return tool["function"]["parameters"]["properties"]["pattern"]["description"]


def test_the_ablation_changes_the_example_and_nothing_else():
    plain, exampled = AST_SEARCH_TOOL["function"], AST_SEARCH_TOOL_WITH_EXAMPLE["function"]
    assert plain["name"] == exampled["name"]
    assert plain["description"] == exampled["description"]
    assert plain["parameters"]["required"] == exampled["parameters"]["required"]
    assert set(plain["parameters"]["properties"]) == set(
        exampled["parameters"]["properties"]
    )
    assert _pattern_doc(AST_SEARCH_TOOL) != _pattern_doc(AST_SEARCH_TOOL_WITH_EXAMPLE)


def test_the_exampled_description_shows_a_pattern_rather_than_naming_the_forms():
    assert "def $F($$$ARGS)" in _pattern_doc(AST_SEARCH_TOOL_WITH_EXAMPLE)


def test_the_ablation_arm_still_passes_the_parity_gate():
    # An ablation that also coached the model would measure coaching. The
    # exampled text may show syntax; it may not say when to reach for the tool.
    assert check_description_parity(ABLATION_TOOLS) == []


def test_ablation_arms_carry_the_exampled_tool_and_controls_carry_the_plain_one():
    for arm, control in ABLATION_CONTROLS.items():
        armed = {id(t) for t in CONDITIONS[arm].tools}
        assert id(AST_SEARCH_TOOL_WITH_EXAMPLE) in armed
        assert id(AST_SEARCH_TOOL) not in armed
        controlled = {id(t) for t in CONDITIONS[control].tools}
        assert id(AST_SEARCH_TOOL) in controlled
        assert id(AST_SEARCH_TOOL_WITH_EXAMPLE) not in controlled


def test_an_ablation_arm_and_its_control_differ_only_in_that_schema():
    for arm, control in ABLATION_CONTROLS.items():
        assert CONDITIONS[arm].tool_names == CONDITIONS[control].tool_names
        assert CONDITIONS[arm].knows == CONDITIONS[control].knows


def test_declarations_gate_passes_as_shipped():
    assert check_ablation_declarations() == []


def test_a_noop_ablation_is_caught(monkeypatch):
    # The dangerous failure: an "ablation" whose schemas match its control, so
    # its runs read as extra samples of the control rather than as a contrast.
    monkeypatch.setattr(conditions, "ABLATION_TOOLS", RETRIEVAL_TOOLS)
    assert any("identical to control" in p for p in check_ablation_declarations())


def test_a_control_that_is_itself_an_ablation_is_caught(monkeypatch):
    monkeypatch.setitem(ABLATION_CONTROLS, "structural_syntax",
                        "structural_lexical_syntax")
    assert any("is itself an ablation" in p
               for p in check_ablation_declarations())


def test_a_pair_differing_in_more_than_the_description_is_caught(monkeypatch):
    wider = conditions.Condition(
        "wider", ("grep", "ast_search", "definition"), "text + syntax",
        description_ablation="x",
    )
    monkeypatch.setitem(CONDITIONS, "wider", wider)
    monkeypatch.setitem(ABLATION_CONTROLS, "wider", "structural_lexical")
    assert any("more than the description" in p
               for p in check_ablation_declarations())


def test_a_declared_ablation_needs_a_named_control(monkeypatch):
    orphan = conditions.Condition(
        "orphan", ("grep", "ast_search"), "text + syntax", description_ablation="x"
    )
    monkeypatch.setitem(CONDITIONS, "orphan", orphan)
    assert any("names no control arm" in p for p in check_ablation_declarations())


def test_the_default_sweep_excludes_the_ablation_arms():
    # Re-running the pilot must not silently acquire two more arms and their
    # cost.
    assert set(PILOT_CONDITIONS).isdisjoint(ABLATION_CONTROLS)
    assert set(ABLATION_CONTROLS.values()) <= set(PILOT_CONDITIONS)


def test_reports_from_several_trees_load_together(tmp_path):
    base, ablation = tmp_path / "base", tmp_path / "abl"
    for directory, condition in ((base, "structural_lexical"),
                                 (ablation, "structural_lexical_syntax")):
        directory.mkdir()
        (directory / "r.json").write_text(json.dumps(
            {"condition": condition, "task_id": "t1", "repeat": 1}))
    loaded = analyze_traces.load_reports(base, ablation)
    assert {r["condition"] for r in loaded} == {
        "structural_lexical", "structural_lexical_syntax"}


def test_the_same_run_appearing_twice_is_refused(tmp_path):
    one, two = tmp_path / "one", tmp_path / "two"
    for directory in (one, two):
        directory.mkdir()
        (directory / "r.json").write_text(json.dumps(
            {"condition": "lexical", "task_id": "t1", "repeat": 1}))
    with pytest.raises(ValueError, match="duplicates run"):
        analyze_traces.load_reports(one, two)
