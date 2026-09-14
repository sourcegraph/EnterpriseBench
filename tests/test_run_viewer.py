"""Pin the run browser's two judgement calls: which context figure, which score scale.

Both exist because the archive is not uniform. A regression in either publishes
numbers that look fine and are wrong by orders of magnitude, so they are tested
from the disk layout rather than by calling the helpers with tidy arguments.
"""
import json
import os
import sys

import pytest

VIEWER = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                      "experiments/run_viewer")
sys.path.insert(0, VIEWER)

pytestmark = pytest.mark.skipif(not os.path.exists(os.path.join(VIEWER, "collect.py")),
                                reason="experiments/run_viewer not present")


def _mod():
    import collect
    return collect


def _write_run(root, task, variant, results, config=None, trace=None):
    run_dir = os.path.join(root, task, variant)
    os.makedirs(run_dir)
    with open(os.path.join(run_dir, "results.json"), "w") as fh:
        json.dump(results, fh)
    if config is not None:
        with open(os.path.join(run_dir, "config.json"), "w") as fh:
            json.dump(config, fh)
    if trace is not None:
        with open(os.path.join(run_dir, "agent_trace.jsonl"), "w") as fh:
            for event in trace:
                fh.write(json.dumps(event) + "\n")
    return run_dir


def _usage(mid, inp, creation, read):
    return {"type": "assistant",
            "message": {"id": mid, "role": "assistant", "content": [],
                        "usage": {"input_tokens": inp,
                                  "cache_creation_input_tokens": creation,
                                  "cache_read_input_tokens": read}}}


def _call(name):
    return {"type": "assistant",
            "message": {"id": "m-" + name, "content": [{"type": "tool_use", "name": name}]}}


class TestContext:
    def test_trace_wins_because_reported_input_excludes_cache_reads(self):
        assert _mod()._context(3_000_000, 2_500) == (3_000_000, "trace")

    def test_reported_wins_when_the_trace_was_only_partly_captured(self):
        value, source = _mod()._context(246_547, 10_062_802)
        assert value == 10_062_802
        assert "incomplete" in source

    def test_reported_is_used_when_there_is_no_trace_at_all(self):
        assert _mod()._context(None, 615_743) == (615_743, "reported")

    def test_neither_available_reports_no_value_rather_than_zero(self):
        assert _mod()._context(None, None) == (None, "")
        assert _mod()._context(None, 0) == (None, "")

    def test_a_streaming_trace_repeating_one_message_is_counted_once(self, tmp_path):
        run = _write_run(str(tmp_path), "t", "baseline", {"scores": {}},
                         trace=[_usage("m1", 10, 20, 30), _usage("m1", 10, 20, 30),
                                _usage("m2", 1, 2, 3)])
        _, _, context = _mod()._scan_trace(run)
        assert context == 60 + 6


class TestScoreScale:
    def test_a_unit_scale_score_is_left_alone(self):
        assert _mod()._normalized({"task_score": 0.75}) == (0.75, False)

    def test_a_weighted_sum_is_divided_by_total_weight(self):
        scores = {"task_score": 3.0,
                  "checkpoints": [{"weight": 1.0}, {"weight": 1.0}, {"weight": 1.0}]}
        assert _mod()._normalized(scores) == (1.0, True)

    def test_uneven_weights_use_their_sum_not_the_checkpoint_count(self):
        scores = {"task_score": 2.0,
                  "checkpoints": [{"weight": 2.0}, {"weight": 2.0}]}
        assert _mod()._normalized(scores) == (0.5, True)

    def test_checkpoint_count_is_the_fallback_when_weights_are_missing(self):
        assert _mod()._normalized({"task_score": 2.0, "checkpoints_total": 4}) == (0.5, True)

    def test_an_unscalable_score_is_dropped_rather_than_published_above_one(self):
        assert _mod()._normalized({"task_score": 3.0}) == (None, False)
        assert _mod()._normalized({}) == (None, False)

    def test_no_collected_run_carries_a_score_outside_the_unit_interval(self):
        """The bug this guards: a mean over mixed scales read 1.306."""
        runs = _mod().collect(os.path.join(os.path.dirname(VIEWER), "..", "results/runs")) \
            if os.path.isdir(os.path.join(os.path.dirname(VIEWER), "..", "results/runs")) else []
        for run in runs:
            if run["score"] is not None:
                assert 0.0 <= run["score"] <= 1.0, f"{run['task']}/{run['variant']}"


class TestCollect:
    def test_tool_names_lose_the_mcp_prefix_so_both_arms_compare(self, tmp_path):
        run = _write_run(str(tmp_path), "t", "mcp_treatment", {"scores": {}},
                         trace=[_call("mcp__sourcegraph__code_finder"), _call("Bash")])
        first, head, _ = _mod()._scan_trace(run)
        assert first == "code_finder"
        assert head == ["code_finder", "Bash"]

    def test_the_opening_sequence_is_capped(self, tmp_path):
        run = _write_run(str(tmp_path), "t", "baseline", {"scores": {}},
                         trace=[_call("read_file_%d" % i) for i in range(40)])
        _, head, _ = _mod()._scan_trace(run)
        assert len(head) == _mod().SEQUENCE_HEAD

    def test_unscored_and_invalidated_runs_stay_out_of_the_index(self, tmp_path):
        root = str(tmp_path)
        _write_run(root, "good", "baseline", {"success": True, "scores": {"task_score": 1.0}})
        os.makedirs(os.path.join(root, "good", "never-ran"))
        _write_run(root, "_invalidated", "some-task", {"scores": {"task_score": 0.0}})
        runs = _mod().collect(root)
        assert [(r["task"], r["variant"]) for r in runs] == [("good", "baseline")]

    def test_a_missing_runs_root_is_an_error_not_an_empty_index(self, tmp_path):
        with pytest.raises(NotADirectoryError):
            _mod().collect(os.path.join(str(tmp_path), "nope"))


def test_the_built_page_is_self_contained_and_carries_every_run(tmp_path):
    sys.path.insert(0, VIEWER)
    import build as builder
    root = str(tmp_path / "runs")
    _write_run(root, "t", "mcp_treatment--x",
               {"success": True, "scores": {"task_score": 0.5, "checkpoints": []},
                "tool_usage": {"total_input_tokens": 900, "cost_usd": 0.5}},
               config={"mode": "mcp_treatment", "variant_label": "x", "model": "claude-sonnet-5"},
               trace=[_usage("m1", 5, 5, 1_000_000), _call("mcp__sg__code_finder")])
    out = str(tmp_path / "index.html")
    runs, _ = builder.build(root, out)
    page = open(out).read()
    assert len(runs) == 1
    assert page.startswith("<!doctype html>")
    assert page.count("<head>") == 1 and page.count("<body>") == 1
    assert "@@" not in page
    for scheme in ("http://", "https://", "//cdn"):
        assert scheme not in page, f"page reaches out to {scheme}"
    assert '"code_finder"' in page
    assert "1000010" in page, "trace-summed context (5 + 5 + 1,000,000) must reach the page"
