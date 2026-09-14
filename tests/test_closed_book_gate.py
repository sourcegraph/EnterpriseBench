"""Tests for the closed-book leakage gate (scripts/validation/verify_closed_book.py)."""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts" / "validation"))

import verify_closed_book as vcb  # noqa: E402


def _make_task(tmp: Path, *, report_rel: str, prompt: str, grep: str) -> Path:
    """Build a minimal task: one check that greps `grep` against a report file."""
    task = tmp / "task"
    (task / "checks").mkdir(parents=True)
    (task / "instruction.md").write_text(prompt)
    check = task / "checks" / "check_one.sh"
    check.write_text(
        "#!/usr/bin/env bash\n"
        'REPORT="${WORKSPACE:-/workspace}/' + report_rel + '"\n'
        'if [[ ! -f "$REPORT" ]]; then printf \'{"score":0.0}\\n\'; exit 0; fi\n'
        f'if grep -qiE \'{grep}\' "$REPORT"; then printf \'{{"score":1.0}}\\n\'; '
        'else printf \'{"score":0.0}\\n\'; fi\n'
    )
    check.chmod(0o755)
    return task


def test_report_path_extraction_handles_report_and_answer_vars(tmp_path: Path):
    task = _make_task(tmp_path, report_rel="out/R.md", prompt="x", grep="foo")
    assert vcb.report_rel_paths(task) == {"out/R.md"}


def test_leaked_checkpoint_detected_when_prompt_satisfies_grep(tmp_path: Path):
    # prompt contains "compile error"; the check greps for it -> leaked
    task = _make_task(tmp_path, report_rel="R.md",
                      prompt="the fix is a compile error in foo", grep="compile.*error")
    r = vcb.measure(task)
    assert r is not None
    assert r.leaked == ("check_one.sh",)
    assert r.echo_mean == 1.0


def test_grounded_checkpoint_not_flagged_when_token_absent_from_prompt(tmp_path: Path):
    # the grep token lives only in the repo, never in the prompt -> not leaked
    task = _make_task(tmp_path, report_rel="R.md",
                      prompt="investigate the incident and report findings",
                      grep="SomeSymbolOnlyInTheRepo")
    r = vcb.measure(task)
    assert r is not None
    assert r.leaked == ()
    assert r.echo_mean == 0.0


def test_always_pass_check_classified_separately_not_as_leak(tmp_path: Path):
    # grep matches even the empty control -> always-pass bug, not leakage
    task = _make_task(tmp_path, report_rel="R.md", prompt="anything",
                      grep="no findings")  # matches EMPTY_REPORT "no findings"
    r = vcb.measure(task)
    assert r is not None
    assert r.always_pass == ("check_one.sh",)
    assert r.leaked == ()


def test_baseline_ratchet_passes_known_and_fails_new(tmp_path: Path):
    task = _make_task(tmp_path, report_rel="R.md",
                      prompt="the fix is a compile error", grep="compile.*error")
    r = vcb.measure(task)
    baseline = tmp_path / "baseline.json"

    # empty baseline -> the leak is "new" -> gate fails (exit 2)
    baseline.write_text(json.dumps({"leaked_checkpoints": {}}))
    assert vcb.main(["--check", "--baseline", str(baseline), str(task)]) == 2

    # baseline records the leak -> gate passes (exit 0)
    baseline.write_text(json.dumps({"leaked_checkpoints": {r.task: list(r.leaked)}}))
    assert vcb.main(["--check", "--baseline", str(baseline), str(task)]) == 0


def test_committed_baseline_is_in_sync_with_corpus():
    """The checked-in baseline must cover every currently-leaked checkpoint,
    otherwise `make verify-closed-book` is already red on main."""
    baseline = vcb.load_baseline(ROOT / "configs" / "closed_book_baseline.json")
    for r in vcb.audit([]):
        allowed = set(baseline.get(r.task, []))
        missing = [c for c in r.leaked if c not in allowed]
        assert not missing, f"{r.task} has unbaselined leaks: {missing}"
