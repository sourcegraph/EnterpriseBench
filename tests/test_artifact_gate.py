"""Tests for the grounded-citations gate (lib/eb_verify/artifact_gate.py).

The gate is what gives ``require_grounded_citations`` teeth in the production
scoring path. Before it existed the flag only shaped the agent's prompt
(run_task.py's _build_instruction_text), so an agent could score 1.0 with
fabricated citations or none at all.

Two properties here are load-bearing and easy to regress:

  - The gate reads ONE pinned path. AnswerValidator globs ``**/answer.json``
    and takes candidates[0], and pathlib yields the anchor directory first —
    so on a world-writable /workspace a decoy at the root beats the real
    agent_output/answer.json that every check script grades. A globbing gate
    would grade a file nothing else grades.
  - Exit code means "did the gate RUN", never "did it PASS", and the verdict
    always lands on stdout. A gate that reported failure via a nonzero exit or
    via stderr would be indistinguishable from a broken harness, and the
    caller would fail open on exactly the case the gate exists to catch.
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "lib"))

from eb_verify.artifact_gate import (
    ANSWER_RELPATH,
    gate_answer_citations,
    main,
)

# A span long enough to clear groundedness.MIN_SPAN_CHARS (20).
REAL_SPAN = "def next_event(self, timeout=None):"
FILE_BODY = f"class Connection:\n    {REAL_SPAN}\n        return PAUSED\n"


@pytest.fixture()
def workspace(tmp_path: Path) -> Path:
    """A workspace shaped like the container's: repos + agent_output."""
    ws = tmp_path / "workspace"
    (ws / "h11" / "h11").mkdir(parents=True)
    (ws / "h11" / "h11" / "_connection.py").write_text(FILE_BODY)
    (ws / "agent_output").mkdir(parents=True)
    return ws


def _write_answer(workspace: Path, payload: dict, *, relpath: str = ANSWER_RELPATH) -> Path:
    path = workspace / relpath
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload))
    return path


def _grounded_payload() -> dict:
    return {
        "answer": "The message originates in h11's state machine.",
        "citations": [
            {"repo": "h11", "file": "h11/_connection.py", "evidence_span": REAL_SPAN}
        ],
    }


# ---------------------------------------------------------------------------
# Verdict semantics
# ---------------------------------------------------------------------------


def test_grounded_citation_passes(workspace: Path):
    answer = _write_answer(workspace, _grounded_payload())
    verdict = gate_answer_citations(answer, workspace)
    assert verdict.passed is True
    assert "score=1.00" in verdict.detail


def test_fabricated_span_fails(workspace: Path):
    """The core cheat: a citation naming a real file with an invented quote."""
    answer = _write_answer(
        workspace,
        {
            "answer": "...",
            "citations": [
                {
                    "repo": "h11",
                    "file": "h11/_connection.py",
                    "evidence_span": "def totally_invented_function(self, nope):",
                }
            ],
        },
    )
    verdict = gate_answer_citations(answer, workspace)
    assert verdict.passed is False
    assert "span_not_found" in verdict.detail


def test_missing_citations_field_fails(workspace: Path):
    """An answer with no citations at all must not sail through the gate."""
    answer = _write_answer(workspace, {"answer": "A confident, unsourced claim."})
    verdict = gate_answer_citations(answer, workspace)
    assert verdict.passed is False
    assert "citations" in verdict.detail


def test_cited_file_missing_fails(workspace: Path):
    answer = _write_answer(
        workspace,
        {
            "citations": [
                {"repo": "h11", "file": "h11/nope.py", "evidence_span": REAL_SPAN}
            ]
        },
    )
    verdict = gate_answer_citations(answer, workspace)
    assert verdict.passed is False
    assert "file_missing" in verdict.detail


def test_too_short_span_fails(workspace: Path):
    """Trivially matchable spans are not evidence (MIN_SPAN_CHARS)."""
    answer = _write_answer(
        workspace,
        {"citations": [{"repo": "h11", "file": "h11/_connection.py", "evidence_span": "return"}]},
    )
    verdict = gate_answer_citations(answer, workspace)
    assert verdict.passed is False
    assert "too_short" in verdict.detail


def test_partially_grounded_fails(workspace: Path):
    """All citations must be grounded — one bad apple fails the gate."""
    answer = _write_answer(
        workspace,
        {
            "citations": [
                {"repo": "h11", "file": "h11/_connection.py", "evidence_span": REAL_SPAN},
                {"repo": "h11", "file": "h11/_connection.py", "evidence_span": "invented span text here"},
            ]
        },
    )
    verdict = gate_answer_citations(answer, workspace)
    assert verdict.passed is False


def test_missing_answer_file_fails(workspace: Path):
    verdict = gate_answer_citations(workspace / ANSWER_RELPATH, workspace)
    assert verdict.passed is False
    assert "no answer" in verdict.detail.lower()


def test_malformed_answer_json_fails(workspace: Path):
    answer = workspace / ANSWER_RELPATH
    answer.parent.mkdir(parents=True, exist_ok=True)
    answer.write_text("{not json")
    verdict = gate_answer_citations(answer, workspace)
    assert verdict.passed is False


def test_answer_json_not_an_object_fails(workspace: Path):
    answer = workspace / ANSWER_RELPATH
    answer.parent.mkdir(parents=True, exist_ok=True)
    answer.write_text('["a list, not an object"]')
    verdict = gate_answer_citations(answer, workspace)
    assert verdict.passed is False


def test_citation_escaping_workspace_is_not_grounded(workspace: Path, tmp_path: Path):
    """A citation into a file outside the workspace must not count as evidence."""
    (tmp_path / "outside.py").write_text(FILE_BODY)
    answer = _write_answer(
        workspace,
        {"citations": [{"repo": "", "file": "../outside.py", "evidence_span": REAL_SPAN}]},
    )
    verdict = gate_answer_citations(answer, workspace)
    assert verdict.passed is False
    assert "path_escape" in verdict.detail


# ---------------------------------------------------------------------------
# CRITICAL-1 regression: the gate must grade the file the checks grade
# ---------------------------------------------------------------------------


def test_decoy_answer_at_workspace_root_is_ignored(workspace: Path):
    """A planted /workspace/answer.json must not shadow agent_output/answer.json.

    /workspace is mode 1777 during the agent's session, so the agent can create
    entries at the root. Every check script pins $WORKSPACE/agent_output/answer.json,
    but AnswerValidator's ``**/answer.json`` glob yields the root decoy FIRST.
    An agent could therefore put its real, uncited answer where the checks grade
    it and a one-citation decoy where a globbing gate would look.
    """
    _write_answer(workspace, _grounded_payload(), relpath="answer.json")  # decoy: grounded
    real = _write_answer(
        workspace,
        {"answer": "real but unsourced", "citations": []},
        relpath=ANSWER_RELPATH,
    )

    verdict = gate_answer_citations(real, workspace)

    # The real artifact has an empty citation list; the decoy is perfectly
    # grounded. If the decoy were consulted this would pass.
    assert verdict.passed is False, "gate consulted the decoy instead of the graded artifact"


def test_empty_citations_list_fails(workspace: Path):
    """check_groundedness scores an empty list 1.0 ('no claims, nothing ungrounded').

    That is right for a metric and wrong for a gate: a task that demands grounded
    citations is not satisfied by an answer that cites nothing.
    """
    answer = _write_answer(workspace, {"answer": "unsourced", "citations": []})
    verdict = gate_answer_citations(answer, workspace)
    assert verdict.passed is False


# ---------------------------------------------------------------------------
# The `python -m` contract run_task depends on
# ---------------------------------------------------------------------------


def _run_module(workspace: Path, answer: Path) -> subprocess.CompletedProcess:
    return subprocess.run(
        [
            sys.executable,
            "-m",
            "eb_verify.artifact_gate",
            "--workspace",
            str(workspace),
            "--answer",
            str(answer),
        ],
        capture_output=True,
        text=True,
        env={
            "PYTHONPATH": str(Path(__file__).resolve().parent.parent / "lib"),
            "PATH": "/usr/bin:/bin",
        },
    )


def test_module_emits_verdict_json_on_stdout_and_exits_zero(workspace: Path):
    answer = _write_answer(workspace, _grounded_payload())
    proc = _run_module(workspace, answer)
    assert proc.returncode == 0
    assert json.loads(proc.stdout)["passed"] is True


def test_module_reports_failure_on_stdout_with_exit_zero(workspace: Path):
    """Exit code answers "did the gate run", never "did it pass".

    A nonzero exit on a failed gate would be indistinguishable from a crashed
    gate, and run_task routes an unreadable gate to the re-run channel — which
    would hand a cheating agent a mulligan instead of a zero.
    """
    answer = _write_answer(
        workspace,
        {"citations": [{"repo": "h11", "file": "h11/_connection.py", "evidence_span": "fabricated span goes here"}]},
    )
    proc = _run_module(workspace, answer)
    assert proc.returncode == 0, "a failed gate must still exit 0 — it RAN"
    verdict = json.loads(proc.stdout)
    assert verdict["passed"] is False
    assert "span_not_found" in verdict["detail"]


def test_module_stdout_is_pure_json(workspace: Path):
    """run_task parses stdout; any stray print would make the verdict unreadable."""
    answer = _write_answer(workspace, _grounded_payload())
    proc = _run_module(workspace, answer)
    json.loads(proc.stdout)  # raises if anything else was printed


def test_main_returns_zero_for_failed_gate(workspace: Path, capsys):
    answer = _write_answer(workspace, {"answer": "no citations here"})
    rc = main(["--workspace", str(workspace), "--answer", str(answer)])
    assert rc == 0
    assert json.loads(capsys.readouterr().out)["passed"] is False
