"""F2 oracle-path-leak gate over the active task corpus.

A ground-truth ``required_files`` / ``sufficient_files`` path appearing
word-boundary-verbatim in ``instruction.md`` leaks the answer location into
the agent-visible prompt (finding code F2, see
``lib/eb_verify/_vendor/benchmark_qa_core/leakage.py``). This gate runs the
checker over every active task and fails on any F2 that has not been
explicitly triaged via a ``[[qa.waivers]]`` block in that task's
``task.toml``.

Waived findings are downgraded to ``info`` by the adapter and pass the gate;
the waiver records the rationale, approver, and bead, so a pass here never
silently hides a leak. Triage precedent: ``docs/qa/eb_f2_triage_2026_06_04.md``
— waive only when the named path is the *given investigation entry point*,
never when it reveals a discovery the task scores.

To resolve a new failure, either redact the path from ``instruction.md``
(real leak) or add a ``[[qa.waivers]]`` entry with a rationale (given entry
point). Do not widen this gate.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from eb_verify.qa_adapter import iter_finding_lines, load_task_inputs, run_qa_checks

ROOT = Path(__file__).parent.parent.parent
BENCHMARKS_DIR = ROOT / "benchmarks"


def _collect_task_tomls() -> list[Path]:
    tasks: list[Path] = []
    for toml_path in sorted(BENCHMARKS_DIR.rglob("task.toml")):
        rel = toml_path.relative_to(BENCHMARKS_DIR)
        if rel.parts[0] in ("mined", "_archived"):
            continue
        tasks.append(toml_path)
    return tasks


TASK_TOMLS = _collect_task_tomls()
TASK_IDS = [str(p.parent.relative_to(BENCHMARKS_DIR)) for p in TASK_TOMLS]


def test_corpus_is_nonempty():
    assert len(TASK_TOMLS) > 0, f"no active tasks found under {BENCHMARKS_DIR}"


@pytest.mark.parametrize("task_toml", TASK_TOMLS, ids=TASK_IDS)
def test_no_unwaived_f2_oracle_path_leak(task_toml: Path):
    report = run_qa_checks(load_task_inputs(task_toml))
    unwaived = [f for f in report.warnings if f.code == "F2"]
    assert not unwaived, (
        "oracle path leaked into instruction.md without a [[qa.waivers]] "
        "triage entry:\n" + "\n".join(iter_finding_lines(unwaived))
    )


@pytest.mark.parametrize("task_toml", TASK_TOMLS, ids=TASK_IDS)
def test_no_stale_f2_waivers(task_toml: Path):
    """Every recorded F2 waiver must still match a live finding.

    A waiver whose token no longer appears in instruction.md is dead weight
    that can silently mask a future re-introduction under an edited message;
    prune it when the instruction or ground truth changes.
    """
    inputs = load_task_inputs(task_toml)
    from eb_verify.qa_adapter import _f2_waivers

    waivers = _f2_waivers(inputs.task_toml)
    if not waivers:
        return
    report = run_qa_checks(inputs)
    waived_messages = " || ".join(
        f.message for f in report.findings if f.code == "F2"
    )
    stale = [
        tok for tok in waivers if f"token {tok!r} " not in waived_messages
    ]
    assert not stale, (
        f"stale [[qa.waivers]] entries (token no longer flagged): {stale}"
    )
