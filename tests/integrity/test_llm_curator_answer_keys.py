"""Every task that declares llm_curator carries a valid answer key.

Tier-2 LLM-curator scoring is a ceiling over the deterministic tier
(final = min(grep_score, judge_score)), and the judge only runs when
expected_solution.json exists next to task.toml. A task that declares
``llm_curator`` in verification_modes without a tracked key therefore
silently skips the judge on a clean checkout — the mode declaration
promises a verification layer that never executes.

This pins the lane ground-truth invariant from the publish campaign:

- Gate A (presence): every active task with ``llm_curator`` in
  verification_modes has an ``expected_solution.json`` sibling. Tasks whose
  checks could not grade an honestly-authored key were downgraded to
  deterministic-only instead — never given a fabricated key.
- Structural validity: every such key passes
  ``validate_expected_solutions.validate_task`` (C1 checkpoint coverage,
  no scaffolds, criteria minima), so a scaffold or partial curation cannot
  ride in under the presence gate.

The wider mode-consistency gates (single-repo strata must not enable the
judge; multi-repo tasks with keys should) live in
``scripts/validation/validate_llm_curator_modes.py`` and are not duplicated
here — this test owns only the promise that a declared judge can run.
"""

from __future__ import annotations

import sys
import tomllib
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
BENCHMARKS = REPO_ROOT / "benchmarks"
SKIP_DIR_NAMES = frozenset({"_archived", "mined"})

sys.path.insert(0, str(REPO_ROOT / "scripts" / "validation"))
from validate_expected_solutions import validate_task  # noqa: E402


def _active_task_dirs() -> list[Path]:
    return sorted(
        tt.parent
        for tt in BENCHMARKS.rglob("task.toml")
        if not any(part in SKIP_DIR_NAMES for part in tt.parts)
    )


def _declares_llm_curator(task_dir: Path) -> bool:
    data = tomllib.loads((task_dir / "task.toml").read_text())
    modes = data.get("verification_modes")
    if not isinstance(modes, list):
        section = data.get("task")
        modes = section.get("verification_modes") if isinstance(section, dict) else None
    return isinstance(modes, list) and "llm_curator" in modes


LLM_CURATOR_TASKS = [d for d in _active_task_dirs() if _declares_llm_curator(d)]
TASK_IDS = [str(d.relative_to(BENCHMARKS)) for d in LLM_CURATOR_TASKS]


def test_corpus_reached() -> None:
    """The parametrized sweep must actually cover a real corpus."""
    assert len(LLM_CURATOR_TASKS) > 0, (
        "no active task declares llm_curator — the sweep below is vacuous, "
        "check the tree-walk"
    )


@pytest.mark.parametrize("task_dir", LLM_CURATOR_TASKS, ids=TASK_IDS)
def test_llm_curator_task_has_valid_answer_key(task_dir: Path) -> None:
    key = task_dir / "expected_solution.json"
    assert key.exists(), (
        f"{task_dir.relative_to(REPO_ROOT)}: verification_modes declares "
        "llm_curator but no expected_solution.json is present — the judge "
        "silently never runs. Author the key from the pinned repos, or "
        "downgrade the task to deterministic-only. Never scaffold a key to "
        "satisfy this test."
    )

    result = validate_task(task_dir)
    assert not result.skipped, f"{task_dir}: validator skipped a present key"
    assert result.ok, (
        f"{task_dir.relative_to(REPO_ROOT)}: expected_solution.json fails "
        f"structural validation: {list(result.errors)}"
    )
