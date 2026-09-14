"""Pin the semantics of the echo-vector coverage measurement.

Every claim this tool's numbers rest on is a pure function over small dicts, and
each was, at some point, wrong in a way that changed the reported figure while
nothing went red. These tests exist so those specific regressions cannot come
back silently:

  - the key scrape lifting ground-truth strings out of list literals, so the
    probe injected the check's own answers as keys and scored itself
  - None-is-zero coercion — the exact conflation the tool exists to expose
  - per-arm maxima subtracted instead of per-shape deltas
  - a signed mean letting one check cancel a real leak on another
  - leaf fields replaced rather than unioned, silently shrinking the probe

Tracking: EnterpriseBench-8xsk5.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT / "scripts" / "validation"))

import echo_vector_coverage as ev  # noqa: E402


# --------------------------------------------------------------------------
# accessed_keys — the scrape that decides what shape the probe builds
# --------------------------------------------------------------------------

def _task_with_check(tmp_path: Path, body: str) -> Path:
    task = tmp_path / "suite" / "task"
    (task / "checks").mkdir(parents=True)
    (task / "checks" / "check_x.sh").write_text(body)
    (task / "instruction.md").write_text("# prompt\n")
    return task


def test_accessed_keys_reads_any_variable_not_just_answer(tmp_path: Path) -> None:
    """Checks bind the deliverable to report/claimed/data, not only `answer`.

    Anchoring to `answer` reported two config-drift tasks as unprobeable when
    they in fact leak.
    """
    task = _task_with_check(tmp_path, """
        report = json.load(f)
        x = report.get('drift_points')
        y = claimed.get('symbols')
        z = data.get('impact')
    """)
    assert set(ev.accessed_keys(task)) == {"drift_points", "symbols", "impact"}


def test_accessed_keys_ignores_list_literals_holding_ground_truth(tmp_path: Path) -> None:
    """THE self-crediting regression.

    A bare `['...']` list literal is ground-truth data, not a subscript. Scraping
    it made the probe inject the check's own answers as JSON keys, which the check
    then grepped back out of json.dumps(answer) — nonsense content scored 0.6 on a
    task with no prompt text present.
    """
    task = _task_with_check(tmp_path, """
        expected = {'reraise_mechanism': ['_reraise_as_remote_protocol_error']}
        terms = ['illegal header line', 'socks_proxy.py']
        real = answer.get('mechanism')
        also = answer['other']
    """)
    keys = set(ev.accessed_keys(task))
    assert keys == {"mechanism", "other"}
    assert "_reraise_as_remote_protocol_error" not in keys
    assert "illegal header line" not in keys


def test_accessed_keys_drops_env_var_reads(tmp_path: Path) -> None:
    """os.environ['REPORT'] is a shell read, not an answer field. Letting these
    through kept the DEFAULT_LEAF_FIELDS fallback from ever firing."""
    task = _task_with_check(tmp_path, """
        REPORT = os.environ['REPORT']
        gt = json.load(open(os.environ['GT_FILE']))
        real = answer.get('findings')
    """)
    assert set(ev.accessed_keys(task)) == {"findings"}


def test_leaf_fields_unions_defaults_rather_than_replacing(tmp_path: Path) -> None:
    """Replacing shrank the probe on 137/141 tasks while being described as
    widening it. The defaults must always be offered."""
    task = _task_with_check(tmp_path, "x = item.get('custom_field')")
    leaves = set(ev.leaf_fields(task))
    assert "custom_field" in leaves
    assert set(ev.DEFAULT_LEAF_FIELDS) <= leaves


# --------------------------------------------------------------------------
# scored_above_zero / mean — None is not zero
# --------------------------------------------------------------------------

def test_scored_above_zero_distinguishes_threw_from_scored_zero() -> None:
    """The whole thesis: a check that THREW is not a check that refused credit."""
    assert ev.scored_above_zero({"c": 0.5}, "c") is True
    assert ev.scored_above_zero({"c": 0.0}, "c") is False
    assert ev.scored_above_zero({"c": None}, "c") is False   # threw, not zero
    assert ev.scored_above_zero({}, "c") is False


def test_mean_is_over_checks_that_ran_and_is_none_when_none_ran() -> None:
    assert ev.mean({"a": 1.0, "b": 0.0}) == 0.5
    assert ev.mean({"a": 1.0, "b": None}) == 1.0   # over what ran — denominator disclosed by caller
    assert ev.mean({"a": None}) is None
    assert ev.mean({}) is None


# --------------------------------------------------------------------------
# best_per_check / per_check_echo_delta — the attribution arithmetic
# --------------------------------------------------------------------------

def test_best_per_check_keeps_none_when_a_check_never_ran() -> None:
    assert ev.best_per_check([{"a": None}, {"a": None}]) == {"a": None}


def test_best_per_check_takes_the_max_and_a_throw_does_not_mask_a_score() -> None:
    assert ev.best_per_check([{"a": None, "b": 0.2}, {"a": 0.7, "b": 0.1}]) == {
        "a": 0.7, "b": 0.2}


def _arms(treatment, nonsense, filler, foreign):
    return {"treatment": treatment, "nonsense": nonsense,
            "filler": filler, "foreign": foreign}


def test_delta_is_credit_beyond_the_strongest_control() -> None:
    arms = _arms([{"c": 1.0}], [{"c": 0.0}], [{"c": 0.2}], [{"c": 0.6}])
    assert ev.per_check_echo_delta(arms) == {"c": 0.4}   # 1.0 - max(0, .2, .6)


def test_delta_is_none_when_no_control_produced_a_verdict() -> None:
    """Unknown is not 'no leak'. `or 0` here reproduces the indicted conflation."""
    arms = _arms([{"c": 1.0}], [{"c": None}], [{"c": None}], [{"c": None}])
    assert ev.per_check_echo_delta(arms) == {"c": None}


def test_delta_is_compared_shape_by_shape_not_per_arm_max() -> None:
    """Shape 0: treatment 0.6, controls 0 -> a real 0.6 leak.
    Shape 1: everything 1.0 -> no signal.
    Per-arm maxima would give max(0.6,1.0) - max(0,1.0) = 0 and erase the leak.
    """
    arms = _arms(
        [{"c": 0.6}, {"c": 1.0}],
        [{"c": 0.0}, {"c": 1.0}],
        [{"c": 0.0}, {"c": 1.0}],
        [{"c": 0.0}, {"c": 1.0}],
    )
    assert ev.per_check_echo_delta(arms) == {"c": 0.6}


def test_delta_skips_shapes_where_the_treatment_threw() -> None:
    arms = _arms(
        [{"c": None}, {"c": 0.5}],
        [{"c": 0.0}, {"c": 0.1}],
        [{"c": 0.0}, {"c": 0.1}],
        [{"c": 0.0}, {"c": 0.1}],
    )
    assert ev.per_check_echo_delta(arms) == {"c": 0.4}


# --------------------------------------------------------------------------
# classify — the md-vector verdict
# --------------------------------------------------------------------------

@pytest.mark.parametrize("scores,expected", [
    ({"a": 0.0, "b": 0.0}, "exercised"),
    ({"a": None, "b": None}, "vacuous"),
    ({"a": 0.0, "b": None}, "partial"),
    ({"a": 0.3, "b": None}, "leak"),
])
def test_classify_partition(scores: dict, expected: str) -> None:
    assert ev.classify(scores) == expected


def test_classify_leak_label_hides_unexercised_checks() -> None:
    """Documents WHY the probe cohort selects on n_threw and not on this label:
    one leaking check wins the label while five never ran."""
    scores = {"a": 0.1, "b": None, "c": None, "d": None, "e": None, "f": None}
    assert ev.classify(scores) == "leak"
    assert sum(1 for v in scores.values() if v is None) == 5


# --------------------------------------------------------------------------
# fill — content goes in every string leaf, shape is preserved
# --------------------------------------------------------------------------

def test_fill_replaces_string_leaves_and_preserves_shape() -> None:
    tpl = {"k": ["x"], "n": 3, "b": True, "d": {"inner": "y"}}
    assert ev.fill(tpl, "T") == {"k": ["T"], "n": 3, "b": True, "d": {"inner": "T"}}


def test_fill_gives_an_empty_list_one_element() -> None:
    assert ev.fill({"k": []}, "T") == {"k": ["T"]}
