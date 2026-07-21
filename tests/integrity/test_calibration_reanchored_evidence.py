"""Re-anchored calibration-005..008 checks grade repo evidence, not the prompt.

The gen-1 ownership/severity checks in these four support_code_mapping
calibration tasks credited concept vocabulary the ticket itself supplies
(ownership_keywords like "sentinel"/"callback"; expected_severity matched as a
substring while the ticket header says "Priority: Medium") plus 0.3 for ANY
non-empty severity value. A schema-shape-valid deliverable whose only content
was prompt text earned up to 1.0 (EnterpriseBench-jn73.2.7.3.1.4).

The re-anchor moves both checks onto ground_truth.json:scoring_evidence —
pinned-repo symbols absent from every instruction*.md — and gates severity on
an EXACT level match. This module pins the three properties of the fixed state
per task:

1. The structured prompt echo scores 0.0 on every check (and the vector is not
   dark: at least one check produces a scored verdict).
2. A hand-built correct answer — fixed literals mirroring the pinned repos
   (click 8.2.1, flask 3.1.0, requests v2.32.3), NOT generated from
   ground_truth.json — earns full credit on every check. Editing
   scoring_evidence to tokens no correct answer contains breaks this leg, so
   evidence drift is caught here rather than in a headline run.
3. A structure-only answer (schema-valid keys, no repo-derived values) earns
   0.0 everywhere — key presence is worth nothing.

The suite-wide invariant (test_structured_prompt_echo.py) enforces property 1
for every non-quarantined JSON task; this module keeps all three enforced for
these tasks regardless of quarantine state.
"""

from __future__ import annotations

import json
import sys
import tempfile
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(REPO_ROOT / "scripts" / "validation"))
import curated_gate_analyzer as gate  # noqa: E402

TASKS = [
    "calibration-005",
    "calibration-006",
    "calibration-007",
    "calibration-008",
]

# Fixed literals mirroring the pinned repos — deliberately NOT built from
# ground_truth.json, so a scoring_evidence edit that no correct answer could
# satisfy fails test_correct_answer_earns_full_credit instead of silently
# zeroing real agents.
CORRECT_ANSWERS: dict[str, dict] = {
    "calibration-005": {
        "code_paths": [
            {"path": "/workspace/click/src/click/core.py", "repo": "click"},
            {"path": "/workspace/click/src/click/types.py", "repo": "click"},
            {"path": "/workspace/click/src/click/decorators.py", "repo": "click"},
        ],
        "error_chain": [
            "Command.parse_args hands parsed opts to Parameter.handle_parse_result",
            "Parameter.consume_value resolves the value and its source",
            "Parameter.process_value runs type_cast_value, which passes the "
            "missing-value marker into ParamType.convert",
            "process_value then invokes the user callback with the un-normalized value",
        ],
        "ownership": "click core parameter processing: Parameter.handle_parse_result"
        " -> process_value -> type_cast_value in src/click/core.py",
        "severity": {
            "level": "medium",
            "rationale": "Scope is limited to option callbacks: consume_value only"
            " yields the marker when no value arrives, and ParamType.convert"
            " already tolerates it; workaround exists",
        },
    },
    "calibration-006": {
        "code_paths": [
            {"path": "/workspace/flask/src/flask/sansio/scaffold.py", "repo": "flask"},
            {"path": "/workspace/flask/src/flask/blueprints.py", "repo": "flask"},
            {"path": "/workspace/flask/src/flask/app.py", "repo": "flask"},
        ],
        "ownership": "Scaffold.errorhandler / register_error_handler and the"
        " error_handler_spec registry in src/flask/sansio/scaffold.py; the"
        " decorator's typevar is bound to ErrorHandlerCallable",
        "severity": {
            "level": "low",
            "rationale": "Typing-only restriction: at runtime blueprint handlers"
            " funnel through handle_user_exception and their return value goes"
            " through make_response, which accepts tuple returns",
        },
    },
    "calibration-007": {
        "code_paths": [
            {"path": "/workspace/requests/src/requests/utils.py", "repo": "requests"},
            {"path": "/workspace/requests/src/requests/sessions.py", "repo": "requests"},
            {"path": "/workspace/requests/src/requests/adapters.py", "repo": "requests"},
        ],
        "ownership": "Session.merge_environment_settings in src/requests/sessions.py:"
        " the trust_env branch reads the CA-bundle env vars, then merge_setting"
        " merges the per-request value over session.verify",
        "severity": {
            "level": "medium",
            "rationale": "Silent TLS-setting override: the same trust_env branch"
            " also honors CURL_CA_BUNDLE, and the merged value is applied in"
            " HTTPAdapter.cert_verify",
        },
    },
    "calibration-008": {
        "code_paths": [
            {"path": "/workspace/click/src/click/testing.py", "repo": "click"},
            {"path": "/workspace/click/src/click/utils.py", "repo": "click"},
            {"path": "/workspace/click/src/click/core.py", "repo": "click"},
        ],
        "ownership": "CliRunner stream management in src/click/testing.py:"
        " make_input_stream builds the BytesIO, isolation wraps it in"
        " EchoingStdin / _NamedTextIOWrapper and swaps sys.stdin",
        "severity": {
            "level": "medium",
            "rationale": "Test-harness only and intermittent: commands resolving"
            " stdin through get_text_stream hit the cached _default_text_stdin"
            " wrapper after isolation() has closed the underlying buffer",
        },
    },
}

# Schema-valid keys, plausible-but-wrong values, zero repo-derived content.
# The severity level is wrong for all four tasks (expected: medium/low/medium/
# medium), matching what a no-investigation submission looks like.
STRUCTURE_ONLY_ANSWER: dict = {
    "code_paths": [{"path": "src/lib/made_up.py", "repo": "click"}],
    "source_files": [],
    "error_chain": ["something happens", "then it breaks"],
    "ownership": "the module that handles this",
    "severity": {"level": "critical", "rationale": "it looked serious"},
    "related_issues": [],
}


def _task_dir(task: str) -> Path:
    return gate.BENCH / "customer_escalation" / task


def _run_with_answer(task: str, answer: dict) -> dict:
    with tempfile.TemporaryDirectory() as td:
        ws = Path(td)
        out = ws / "agent_output"
        out.mkdir(parents=True)
        (out / "answer.json").write_text(json.dumps(answer, indent=2))
        return gate.run_checks(_task_dir(task), ws)


@pytest.mark.parametrize("task", TASKS)
def test_structured_echo_scores_zero(task: str) -> None:
    """Both echo payload shapes, every prompt variant: every check 0.0, and the
    vector actually evaluates the task (no vacuous None-only pass)."""
    scores = gate.structured_echo_scores(_task_dir(task))
    assert scores, f"{task}: structured vector produced no runs"
    evaluated = [v for per in scores.values() for v in per.values() if v is not None]
    assert evaluated, f"{task}: every check errored on the structured echo — dark"
    assert not gate.structured_leaks(scores), (
        f"{task}: a schema-valid deliverable whose only content is prompt text "
        f"still earns credit: {gate.structured_leaks(scores)}"
    )


@pytest.mark.parametrize("task", TASKS)
def test_correct_answer_earns_full_credit(task: str) -> None:
    """A genuinely correct answer keeps the credit the checkpoints intend —
    the re-anchor must not zero the right answer along with the echo."""
    scores = _run_with_answer(task, CORRECT_ANSWERS[task])
    assert scores, f"{task}: no checks ran"
    for check, score in scores.items():
        assert score is not None and score >= 1.0, (
            f"{task}: correct answer scored {score} on {check}; the re-anchored "
            f"evidence is not satisfiable by a correct investigation"
        )


@pytest.mark.parametrize("task", TASKS)
def test_structure_only_answer_earns_zero(task: str) -> None:
    """Key presence must earn nothing: only values requiring repo access score."""
    scores = _run_with_answer(task, STRUCTURE_ONLY_ANSWER)
    assert scores, f"{task}: no checks ran"
    for check, score in scores.items():
        assert score is not None and score == 0.0, (
            f"{task}: structure-only answer scored {score} on {check}"
        )


@pytest.mark.parametrize("task", TASKS)
def test_scoring_evidence_present_for_both_checkpoints(task: str) -> None:
    """The checks hard-depend on scoring_evidence[ownership|severity]; a GT
    regression here turns every real run into a VERIFIER_INFRA_ERROR 0.0."""
    gt = json.loads((_task_dir(task) / "ground_truth.json").read_text())
    evidence = gt.get("scoring_evidence")
    assert isinstance(evidence, dict), f"{task}: scoring_evidence missing"
    for checkpoint in ("ownership", "severity"):
        tokens = evidence.get(checkpoint)
        assert isinstance(tokens, list) and len(tokens) >= 2, (
            f"{task}: scoring_evidence[{checkpoint}] needs >=2 tokens, "
            f"got {tokens!r}"
        )
