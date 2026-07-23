"""No task may direct its graded deliverable inside a gated repo dir.

Root cause (2026-07-22, root-causing the mcp_only sweep): a gated arm's
mode_gate denies the agent read/write on every [[repos]].path tree, but
41 tasks directed the deliverable write to /workspace/<repo>/<file> --
inside that tree. The write EACCES'd, the checkpoint reported the file
"not found", and every checkpoint scored 0.0: not a retrieval failure, a
harness contract violation. Migrated to /workspace/agent_output/ (the one
location mode_gate deliberately leaves writable in every arm; see its
lockdown_commands docstring) by
scripts/authoring/migrate_deliverable_to_agent_output.py.

This is the permanent gate against a NEW task reintroducing the same bug.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
BENCH = REPO_ROOT / "benchmarks"
sys.path.insert(0, str(REPO_ROOT / "scripts" / "authoring"))

from migrate_deliverable_to_agent_output import find_gated_deliverables  # noqa: E402

HITS = find_gated_deliverables(BENCH)


def test_no_active_task_grades_a_deliverable_inside_a_gated_repo():
    if not HITS:
        return
    detail = "\n".join(
        f"  {h['task_dir'].relative_to(REPO_ROOT)}: {h['old_path']}" for h in HITS
    )
    pytest.fail(
        f"{len(HITS)} task(s) grade a deliverable inside a repo dir mode_gate "
        f"locks in gated arms -- the write EACCESs and every checkpoint scores "
        f"a false 0.0. Route the deliverable to /workspace/agent_output/ "
        f"(scripts/authoring/migrate_deliverable_to_agent_output.py --apply "
        f"<task_dir>):\n{detail}"
    )


def test_the_scan_is_actually_covering_tasks():
    """Guard against a silently empty sweep: if the [[repos]].path parsing or
    the checks/*.sh declaration pattern stops matching, this test would pass
    vacuously while a real regression slips through."""
    covered = list(BENCH.glob("*/*/checks/*.sh"))
    covered = [
        p for p in covered if "_archived" not in p.parts and "mined" not in p.parts
    ]
    assert len(covered) >= 400, (
        f"only {len(covered)} check scripts found under benchmarks/ -- the glob "
        f"or the corpus layout changed; this invariant needs re-pointing, not "
        f"a silent pass"
    )
