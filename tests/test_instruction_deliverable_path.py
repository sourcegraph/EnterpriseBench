"""The output appendix must name the deliverable the checks actually grade.

EnterpriseBench-uj43y: `_build_instruction_text` used to append a fixed
"write your findings as a JSON file to /workspace/agent_output/answer.json"
to EVERY task, while 40 of the 139 active tasks have Tier-1 checks that grade
a bespoke filename (INCIDENT_REPORT.md, DRIFT_REPORT.json, ...). Agents that
followed the appendix wrote a file nothing grades and scored 0.0 with all the
retrieval work done; agents that followed instruction.md scored normally. The
split was luck, so ~30% of the corpus carried coin-flip scoring noise.

The appendix now names the task's own graded deliverable, and only promises
JSON when the deliverable is JSON.

Also covered: the corpus-wide invariant that every graded deliverable is
named by the instruction the agent reads.
"""

from __future__ import annotations

import re
import subprocess
import sys
import tomllib
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT / "scripts" / "orchestration"))
sys.path.insert(0, str(REPO_ROOT / "lib"))

from run_task import (  # noqa: E402
    ANSWER_ARTIFACT_PATH,
    _build_instruction_text,
    _derive_graded_deliverable,
)

# A graded deliverable as a check script names it: agent_output/<file>.
_CHECK_DELIVERABLE_RE = re.compile(
    r"agent_output/([A-Za-z0-9_.-]+\.(?:json|md|txt|ya?ml))"
)

_ANSWER_FILES = {"answer.json", "answer.txt"}


def _write_task(tmp_path: Path, instruction: str) -> Path:
    task_dir = tmp_path / "task"
    task_dir.mkdir()
    (task_dir / "instruction.md").write_text(instruction)
    return task_dir


# ---------------------------------------------------------------------------
# _derive_graded_deliverable
# ---------------------------------------------------------------------------


def test_derives_bespoke_deliverable_named_by_instruction(tmp_path):
    task_dir = _write_task(
        tmp_path,
        "Find the drift.\n\nWrite your findings to "
        "`/workspace/agent_output/DRIFT_REPORT.json` as a JSON object.\n",
    )
    assert (
        _derive_graded_deliverable(task_dir)
        == "/workspace/agent_output/DRIFT_REPORT.json"
    )


def test_derives_markdown_deliverable(tmp_path):
    task_dir = _write_task(
        tmp_path,
        "Write the report to `/workspace/agent_output/INCIDENT_REPORT.md`.\n",
    )
    assert (
        _derive_graded_deliverable(task_dir)
        == "/workspace/agent_output/INCIDENT_REPORT.md"
    )


def test_falls_back_to_answer_when_instruction_names_none(tmp_path):
    task_dir = _write_task(tmp_path, "Investigate the failure and report back.\n")
    assert _derive_graded_deliverable(task_dir) == ANSWER_ARTIFACT_PATH


def test_answer_json_mention_is_not_a_bespoke_deliverable(tmp_path):
    task_dir = _write_task(
        tmp_path, "Write to `/workspace/agent_output/answer.json`.\n"
    )
    assert _derive_graded_deliverable(task_dir) == ANSWER_ARTIFACT_PATH


def test_ambiguous_multi_deliverable_falls_back_to_answer(tmp_path):
    """Two graded files is not a shape the appendix can name; keep the default.

    Silently picking one would tell the agent to skip the other.
    """
    task_dir = _write_task(
        tmp_path,
        "Write `/workspace/agent_output/RESOLUTION.json` and "
        "`/workspace/agent_output/INVESTIGATION.md`.\n",
    )
    assert _derive_graded_deliverable(task_dir) == ANSWER_ARTIFACT_PATH


def test_repo_input_paths_are_not_mistaken_for_deliverables(tmp_path):
    """Only /workspace/agent_output/ is a deliverable location.

    Instructions routinely cite input files under a repo tree; naming one as
    the output would send the write into a mode_gate-locked path (aab54ec).
    """
    task_dir = _write_task(
        tmp_path,
        "Read `/workspace/consul/values.yaml` and `/workspace/consul/README.md`, "
        "then report what you find.\n",
    )
    assert _derive_graded_deliverable(task_dir) == ANSWER_ARTIFACT_PATH


# ---------------------------------------------------------------------------
# appendix rendering
# ---------------------------------------------------------------------------


def test_appendix_names_bespoke_json_deliverable(tmp_path):
    task_dir = _write_task(
        tmp_path,
        "Write your findings to `/workspace/agent_output/DRIFT_REPORT.json`.\n",
    )
    text = _build_instruction_text(task_dir, mode="baseline")

    assert "/workspace/agent_output/DRIFT_REPORT.json" in text
    # The old fixed appendix must not send the agent to an ungraded file.
    assert ANSWER_ARTIFACT_PATH not in text
    assert "```json" in text  # JSON deliverable still gets the shape example


def test_appendix_does_not_demand_json_for_a_markdown_deliverable(tmp_path):
    task_dir = _write_task(
        tmp_path,
        "Write the report to `/workspace/agent_output/INCIDENT_REPORT.md`.\n",
    )
    text = _build_instruction_text(task_dir, mode="baseline")

    assert "/workspace/agent_output/INCIDENT_REPORT.md" in text
    assert ANSWER_ARTIFACT_PATH not in text
    assert "```json" not in text
    assert "as a JSON file" not in text


def test_answer_json_tasks_keep_the_canonical_appendix(tmp_path):
    task_dir = _write_task(tmp_path, "Trace the error to its source.\n")
    text = _build_instruction_text(task_dir, mode="baseline")

    assert ANSWER_ARTIFACT_PATH in text
    assert "```json" in text
    assert "mkdir -p /workspace/agent_output" in text


def test_appendix_always_creates_the_output_directory(tmp_path):
    task_dir = _write_task(
        tmp_path, "Write to `/workspace/agent_output/SCHEMA_IMPACT.md`.\n"
    )
    text = _build_instruction_text(task_dir, mode="baseline")
    assert "mkdir -p /workspace/agent_output" in text


def test_grounded_citations_pin_the_deliverable_to_answer_json(tmp_path):
    """The groundedness gate reads answer.json, so citations pin it there.

    lib/eb_verify/plugins/answer.py resolves the graded artifact by name; a
    bespoke file would carry citations no verifier ever reads.
    """
    task_dir = _write_task(
        tmp_path,
        "Write your findings to `/workspace/agent_output/DRIFT_REPORT.json`.\n",
    )
    text = _build_instruction_text(
        task_dir, mode="baseline", require_grounded_citations=True
    )

    # The body still names DRIFT_REPORT.json; what must not happen is the
    # appendix directing the citation-bearing write there.
    appendix = text.split("## Output Requirements", 1)[1]
    assert ANSWER_ARTIFACT_PATH in appendix
    assert "citations" in appendix
    assert "DRIFT_REPORT.json" not in appendix


def test_missing_instruction_still_returns_none(tmp_path):
    empty = tmp_path / "task"
    empty.mkdir()
    assert _build_instruction_text(empty, mode="baseline") is None


# ---------------------------------------------------------------------------
# corpus invariant
# ---------------------------------------------------------------------------


def _active_task_dirs() -> list[Path]:
    listed = subprocess.run(
        ["git", "ls-files", "benchmarks/**/task.toml"],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        check=True,
    ).stdout.split()
    return [
        REPO_ROOT / path
        for path in (p for p in listed if "_archived" not in p)
    ]


def _graded_deliverables(task_dir: Path) -> set[str]:
    names: set[str] = set()
    for check in sorted((task_dir / "checks").glob("*.sh")):
        names |= set(_CHECK_DELIVERABLE_RE.findall(check.read_text()))
    return {n for n in names if n not in _ANSWER_FILES}


def test_every_graded_deliverable_is_named_by_its_instruction():
    """A check may only grade a file the agent was told to write.

    Without this, an agent can do the retrieval correctly and still score 0.0
    because the graded filename exists nowhere in its instructions — the
    uj43y failure mode, re-entering through a newly authored task.

    session_type="chain" tasks are exempt: they are not agent-runnable under
    the single-session arms and carry no instruction.md output section.
    """
    offenders = []
    for toml_path in _active_task_dirs():
        task_dir = toml_path.parent
        instruction = task_dir / "instruction.md"
        if not instruction.exists():
            continue
        if 'session_type = "chain"' in toml_path.read_text():
            continue
        graded = _graded_deliverables(task_dir)
        if not graded:
            continue
        named = set(
            re.findall(
                r"/workspace/agent_output/([A-Za-z0-9_.-]+\.(?:json|md|txt|ya?ml))",
                instruction.read_text(),
            )
        )
        missing = graded - named
        if missing:
            offenders.append(f"{task_dir.name}: checks grade {sorted(missing)}")

    assert not offenders, "graded deliverable never named in instruction.md:\n" + "\n".join(
        offenders
    )


def test_no_task_requires_citations_and_a_bespoke_deliverable():
    """Citations pin the write to answer.json; a bespoke deliverable moves it.

    A task asking for both would put the appendix back in conflict with its own
    instruction body — the uj43y shape, one level down. No such task exists;
    this keeps it that way.
    """
    conflicts = []
    for toml_path in _active_task_dirs():
        task_dir = toml_path.parent
        with toml_path.open("rb") as handle:
            task_data = tomllib.load(handle)
        if not (task_data.get("ground_truth") or {}).get(
            "require_grounded_citations", False
        ):
            continue
        if _derive_graded_deliverable(task_dir) != ANSWER_ARTIFACT_PATH:
            conflicts.append(task_dir.name)

    assert not conflicts, (
        "task requires grounded citations but names a non-answer.json "
        f"deliverable: {conflicts}"
    )


@pytest.mark.parametrize("mode", ["baseline", "mcp_only", "cli"])
def test_appendix_deliverable_matches_checks_across_the_corpus(mode):
    """End to end: for every bespoke task, the built instruction points the
    agent at the file its own checks read."""
    mismatches = []
    for toml_path in _active_task_dirs():
        task_dir = toml_path.parent
        if 'session_type = "chain"' in toml_path.read_text():
            continue
        graded = _graded_deliverables(task_dir)
        if len(graded) != 1:
            continue
        text = _build_instruction_text(task_dir, mode=mode)
        if text is None:
            continue
        appendix = text.split("## Output Requirements", 1)[1]
        expected = f"/workspace/agent_output/{graded.pop()}"
        if expected not in appendix or ANSWER_ARTIFACT_PATH in appendix:
            mismatches.append(task_dir.name)

    assert not mismatches, f"appendix points away from the graded file: {mismatches}"
