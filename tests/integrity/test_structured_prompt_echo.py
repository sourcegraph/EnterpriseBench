"""Structured prompt-echo invariant for JSON-deliverable tasks.

The md-grep echo vector (`cp instruction.md <deliverable>`, enforced by
test_report_prompt_echo.py) is structurally blind to JSON deliverables: the
prompt copy is not valid JSON, json.load throws inside every check, every check
returns 0.0/None, and the task is certified clean without the vector ever
exercising its grading logic (EnterpriseBench-jn73.2.7.3.1.4). config-drift-001
was 100% prompt-solvable while "clean" under that gate.

This test closes the blind spot with the STRUCTURED echo vector
(curated_gate_analyzer.structured_echo_scores): for every JSON deliverable it
synthesizes a deliverable that is schema-shape-valid — the key structure is
mirrored from ground_truth.json plus every top-level key the task's own checks
read out of the parsed answer — but whose every string field value is the
task's agent-visible prompt text (each instruction*.md variant, separately).
Numbers, booleans and the answer key's own string values are neutralized so no
credit can come from ground-truth content riding along; the ONLY payload is
prompt text. A check that awards > 0 on that deliverable is grading vocabulary
the prompt already handed the agent, not investigation evidence.

Invariant: every active JSON-deliverable task scores 0.0 on the structured echo
on every check that evaluates it, for every prompt variant. Known leakers are
QUARANTINED as strict xfails (known_structured_echo_leaks.json): fixing one
flips its xfail to a failure that says "remove me from the quarantine" — the
list only shrinks. A NEW leak is a failure to fix, never an addition.

The vector must also actually bite: a JSON task whose every check errors on a
schema-shape-valid deliverable is still dark, so the clean test additionally
asserts at least one check produced a scored verdict.

Tracking: EnterpriseBench-jn73.2.7.3.1.4.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(REPO_ROOT / "scripts" / "validation"))
import curated_gate_analyzer as gate  # noqa: E402

QUARANTINE_FILE = Path(__file__).parent / "known_structured_echo_leaks.json"
KNOWN_LEAK: set[str] = set(json.loads(QUARANTINE_FILE.read_text()))

JSON_TASKS = [
    d
    for d in gate.discover_report_tasks()
    if any(p.endswith(".json") for p in gate.deliverable_paths(d))
]
TASK_IDS = [str(d.relative_to(gate.BENCH)) for d in JSON_TASKS]


# --- Synthesis unit tests: the vector must carry prompt text and nothing else -


def test_echofy_neutralizes_every_answer_leaf() -> None:
    """String leaves become the prompt; numeric/bool leaves become 0/False; the
    key structure survives. No ground-truth VALUE may ride into the echo — a
    check crediting it would be flagged as a prompt leak it is not."""
    shape = {
        "file": "src/flask/blueprints.py",
        "confidence": 0.95,
        "line": 1423,
        "hit": True,
        "chain": ["step one", "step two"],
        "nested": {"secret": "containerPorts.serfLAN"},
        "empty": [],
        "absent": None,
    }
    out = gate._echofy(shape, "PROMPT")
    assert out == {
        "file": "PROMPT",
        "confidence": 0,
        "line": 0,
        "hit": False,
        "chain": ["PROMPT", "PROMPT"],
        "nested": {"secret": "PROMPT"},
        "empty": [],
        "absent": None,
    }


def test_synthesized_echo_is_valid_json_with_check_read_keys(tmp_path: Path) -> None:
    """The synthesized deliverable parses as JSON, mirrors the ground-truth
    shape, and grafts the keys the task's checks read from the answer."""
    (tmp_path / "checks").mkdir()
    (tmp_path / "checks" / "check_x.sh").write_text(
        "python3 -c \"a = answer.get('source_files', answer.get('files', []))\"\n"
    )
    (tmp_path / "ground_truth.json").write_text(
        json.dumps({"error_chain": ["a", "b"], "confidence": 0.9})
    )
    echo = json.loads(gate.synthesize_structured_echo(tmp_path, "PROMPT TEXT"))
    assert echo["error_chain"] == ["PROMPT TEXT", "PROMPT TEXT"]
    assert echo["confidence"] == 0
    assert echo["source_files"] == ["PROMPT TEXT"]
    assert echo["files"] == ["PROMPT TEXT"]


def test_every_task_synthesis_contains_no_ground_truth_string() -> None:
    """Per-task: no ground-truth string value survives into the echo unless the
    prompt itself contains it. Guarantees a structured-echo leak really is
    prompt credit, never answer-key credit smuggled in by the synthesizer."""
    for task_dir in JSON_TASKS:
        gt = json.loads((task_dir / "ground_truth.json").read_text())
        for instr in sorted(task_dir.glob("instruction*.md")):
            prompt = instr.read_text(errors="replace")
            for echo in (
                gate.synthesize_structured_echo(task_dir, prompt),
                gate.synthesize_structured_list_echo(task_dir, prompt),
            ):
                payload = json.loads(echo)

                def leaf_strings(node):
                    if isinstance(node, str):
                        yield node
                    elif isinstance(node, dict):
                        for v in node.values():
                            yield from leaf_strings(v)
                    elif isinstance(node, list):
                        for v in node:
                            yield from leaf_strings(v)

                assert all(s == prompt for s in leaf_strings(payload)), (
                    f"{task_dir.name}/{instr.name}: a non-prompt string leaf "
                    f"survived synthesis"
                )
                for value in leaf_strings(gt):
                    if len(value) > 3 and value not in prompt:
                        assert value not in echo, (
                            f"{task_dir.name}/{instr.name}: ground-truth value "
                            f"{value!r} leaked into the structured echo"
                        )


# --- The invariant -----------------------------------------------------------


@pytest.mark.parametrize("task_rel", [t for t in TASK_IDS if t not in KNOWN_LEAK])
def test_json_task_resists_structured_echo(task_rel: str) -> None:
    """Every non-quarantined JSON task: all checks 0.0 on the structured echo,
    and at least one check actually evaluated it (no vacuous pass)."""
    scores = gate.structured_echo_scores(gate.BENCH / task_rel)
    assert scores, f"{task_rel}: structured vector produced no runs"

    evaluated = [
        v for per_check in scores.values() for v in per_check.values() if v is not None
    ]
    assert evaluated, (
        f"{task_rel}: every check errored on a schema-shape-valid deliverable — "
        f"the structured vector does not exercise this task; it is still dark"
    )

    leak = gate.structured_leaks(scores)
    assert not leak, (
        f"{task_rel} credits a schema-valid deliverable whose only content is "
        f"prompt text: {leak}. Re-anchor these checks to non-prompt evidence "
        f"(ground_truth.json:scoring_evidence / evidence_tokens)."
    )


@pytest.mark.parametrize("task_rel", sorted(KNOWN_LEAK))
@pytest.mark.xfail(strict=True, reason="known structured-echo leak, quarantined "
                   "under EnterpriseBench-jn73.2.7.3.1.4")
def test_quarantined_task_still_leaks(task_rel: str) -> None:
    """When a quarantined task is fixed this strict xfail flips (XPASS ->
    failure), signalling: remove it from known_structured_echo_leaks.json."""
    leak = gate.structured_leaks(gate.structured_echo_scores(gate.BENCH / task_rel))
    assert not leak


def test_quarantine_list_has_no_stale_entries() -> None:
    stale = KNOWN_LEAK - set(TASK_IDS)
    assert not stale, f"quarantine references non-JSON or retired tasks: {stale}"


def test_the_sweep_is_actually_covering_tasks() -> None:
    """Guard against a silently empty sweep: 52 active JSON-deliverable tasks
    exposed a JSON deliverable when this was written. A large drop means
    deliverable extraction moved and the gate is passing vacuously."""
    assert len(TASK_IDS) >= 45, (
        f"only {len(TASK_IDS)} JSON-deliverable tasks discovered; 52 carried a "
        f".json deliverable when this was written"
    )
