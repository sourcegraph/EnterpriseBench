"""End-to-end plumbing test for the condition sweep, with no model call.

A scripted generation seam stands in for the provider, so this exercises the
real path a sweep takes -- condition tool surface, backend dispatch through the
shared router, instrumentation, aligned scoring, report shape -- against a real
task and real local clones. It is what makes "the sweep is ready to run" a
checked claim rather than an expectation.
"""

import json
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(HERE.parent / "toast_retrieval"))

import run_conditions
from analyze_traces import summarize_run
from backends import Instrumented
from conditions import CONDITIONS
from run_claude_policy import run_one

TASKS = json.loads((HERE / "slice_tasks.json").read_text())


def _scripted(script):
    """A generate() that replays `script`: a list of (tool, arguments) rounds."""
    rounds = iter(script)

    def generate(messages, *, force_submit=False):
        calls = next(rounds)
        return SimpleNamespace(
            choices=[
                SimpleNamespace(
                    message=SimpleNamespace(
                        content="",
                        tool_calls=[
                            SimpleNamespace(
                                id=f"call{index}",
                                function=SimpleNamespace(
                                    name=name, arguments=json.dumps(arguments)
                                ),
                            )
                            for index, (name, arguments) in enumerate(calls)
                        ],
                    )
                )
            ],
            usage=SimpleNamespace(prompt_tokens=10, completion_tokens=5),
        )

    return generate


@pytest.fixture()
def candidate():
    """A real single-repo Python task whose clone is on disk."""
    for task in TASKS:
        if task["task_id"] == "calibration-002":
            return task
    pytest.skip("calibration-002 not in the slice manifest")


@pytest.mark.parametrize(
    "condition_name,first_call",
    [
        ("lexical", ("grep", {"pattern": "Context"})),
        ("structural", ("ast_search", {"pattern": "class $C:", "language": "python"})),
        (
            "codeintel",
            ("definition", {"repo": "github.com/sg-evals/click--8.2.1",
                            "path": "src/click/core.py", "symbol": "Context"}),
        ),
    ],
)
def test_a_run_completes_and_scores_in_each_condition(
    candidate, condition_name, first_call, monkeypatch
):
    if condition_name == "codeintel":
        # The index is a network dependency; its live behaviour is probed
        # separately, not pinned in an offline test.
        import backends

        monkeypatch.setattr(
            backends.sg_cli,
            "call_tool",
            lambda tool, arguments: (
                "# github.com/sg-evals/click--8.2.1 – src/click/core.py\n168: class Context:\n",
                False,
            ),
        )

    recorder = Instrumented(CONDITIONS[condition_name].backend())
    generate = _scripted(
        [
            [first_call],
            [("read_file", {"repo": candidate["store_identifiers"][0], "path": "src/click/core.py"})],
            [("submit_files", {"files": [{"path": "src/click/core.py"}]})],
        ]
    )
    report = run_one(candidate, recorder, generate, "scripted", policy=f"condition:{condition_name}")

    assert report["rounds_executed"] == 3
    assert report["retrieved_paths"] == ["src/click/core.py"]
    assert report["ir_scores"]["n_ground_truth"] > 0
    assert report["scoring"] == "aligned_suffix_v1"

    log = recorder.to_log()
    assert [entry["tool"] for entry in log] == [first_call[0], "read_file"]
    assert log[0]["seconds"] >= 0
    # The read actually returned the file, so its recorded size is nonzero --
    # proof the backend reached a real clone rather than silently missing.
    assert log[1]["result_chars"] > 0


def test_a_condition_never_answers_a_tool_it_does_not_offer(candidate):
    """The lexical arm must refuse ast_search even if the model emits one."""
    recorder = Instrumented(CONDITIONS["lexical"].backend())
    generate = _scripted(
        [
            [("ast_search", {"pattern": "class $C:"})],
            [("submit_files", {"files": [{"path": "src/click/core.py"}]})],
        ]
    )
    run_one(candidate, recorder, generate, "scripted", policy="condition:lexical")
    entry = recorder.to_log()[0]
    assert entry["tool"] == "ast_search"
    assert entry["hit_paths"] == []


def test_run_units_are_shuffled_within_a_repeat_and_reproducible():
    candidates = [{"task_id": f"t{i}"} for i in range(4)]
    names = ["lexical", "structural"]
    first = run_conditions._units(candidates, names, repeats=2, seed=7)
    again = run_conditions._units(candidates, names, repeats=2, seed=7)
    assert first == again
    assert len(first) == 16
    assert first != run_conditions._units(candidates, names, repeats=2, seed=8)
    # Every repeat block holds every (condition, task) pair exactly once.
    for repeat in (1, 2):
        block = [(c, t["task_id"]) for c, r, t in first if r == repeat]
        assert sorted(block) == sorted(
            (name, t["task_id"]) for name in names for t in candidates
        )


def test_report_carries_enough_to_analyze_behaviour(candidate):
    recorder = Instrumented(CONDITIONS["structural_lexical"].backend())
    generate = _scripted(
        [
            [("grep", {"pattern": "class Context"})],
            [("ast_search", {"pattern": "class $C:", "language": "python"})],
            [("submit_files", {"files": [{"path": "src/click/core.py"}]})],
        ]
    )
    report = run_one(candidate, recorder, generate, "scripted", policy="condition:structural_lexical")
    condition = CONDITIONS["structural_lexical"]
    report.update(
        condition=condition.name,
        condition_tools=[t["function"]["name"] for t in condition.tools],
        condition_knows=condition.knows,
        repeat=1,
        tool_log=recorder.to_log(),
        tool_seconds=1.0,
        retrieval_result_chars=10,
    )
    summary = summarize_run(report)
    assert summary["first_primitive"] == "lexical"
    assert summary["escalated_from_lexical"] == "structural"
    assert summary["unused_search_tools"] == []


def test_only_code_intel_conditions_are_gated_on_the_index():
    """Structural and lexical read the clone; index state cannot bear on them."""
    assert run_conditions._needs_code_intel("codeintel")
    assert run_conditions._needs_code_intel("combined")
    assert not run_conditions._needs_code_intel("lexical")
    assert not run_conditions._needs_code_intel("structural")
    assert not run_conditions._needs_code_intel("structural_lexical")


def _write_coverage(tmp_path, monkeypatch, payload):
    path = tmp_path / "index_coverage.json"
    path.write_text(json.dumps(payload))
    monkeypatch.setattr(run_conditions, "COVERAGE", path)


def test_unprobed_coverage_is_not_an_empty_exclusion(tmp_path, monkeypatch):
    """A store the probe never covered must stop the sweep, not pass silently."""
    _write_coverage(
        tmp_path,
        monkeypatch,
        {"stores": {"github.com/x/a": {}}, "code_intel_ineligible_tasks": []},
    )
    candidates = [{"task_id": "t1", "store_identifiers": ["github.com/x/b"]}]
    assert run_conditions._code_intel_ineligible(candidates) is None


def test_missing_coverage_file_is_not_an_empty_exclusion(tmp_path, monkeypatch):
    monkeypatch.setattr(run_conditions, "COVERAGE", tmp_path / "absent.json")
    assert run_conditions._code_intel_ineligible([]) is None


def test_ineligible_tasks_are_reported_when_coverage_is_complete(tmp_path, monkeypatch):
    _write_coverage(
        tmp_path,
        monkeypatch,
        {
            "stores": {"github.com/x/a": {}, "github.com/x/b": {}},
            "code_intel_ineligible_tasks": [{"task_id": "t2", "stores": {}}],
        },
    )
    candidates = [
        {"task_id": "t1", "store_identifiers": ["github.com/x/a"]},
        {"task_id": "t2", "store_identifiers": ["github.com/x/b"]},
    ]
    assert run_conditions._code_intel_ineligible(candidates) == {"t2"}
