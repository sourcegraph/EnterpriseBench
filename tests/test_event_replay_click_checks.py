"""event-replay-click-ci-001 checks emit the full {score, passed, message} verdict.

These four bash checks predate the corpus-wide verdict convention and emitted
{"score", "message"} only. The runtime parser (guard_checkpoint_verdict)
tolerates a missing 'passed' by deriving it from score, but the audit gate
(scripts/audit_consistency.py, CI's "Audit task consistency" step) requires
the explicit key — and an explicit bool means no caller ever derives the
other half of the verdict differently. This test executes each script's
three branches (pass / fail / missing actions.jsonl) and asserts the emitted
JSON carries a numeric score and a boolean passed consistent with it.
"""

from __future__ import annotations

import json
import subprocess
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
CHECKS_DIR = (
    REPO_ROOT
    / "benchmarks"
    / "incident_response"
    / "event-replay-click-ci-001"
    / "checks"
)

# One actions.jsonl line that satisfies each script's grep.
# check_root_cause.sh needs BOTH an "investigate" action and a
# markupsafe/utils/soft_str mention.
_PASSING_ACTION = {
    "check_communication.sh": '{"action": "communicate", "message": "found it"}',
    "check_remediation.sh": '{"action": "remediate", "detail": "pin markupsafe"}',
    "check_root_cause.sh": '{"action": "investigate", "target": "markupsafe"}',
    "check_triage.sh": '{"action": "triage", "severity": "high"}',
}

SCRIPTS = sorted(p.name for p in CHECKS_DIR.glob("check_*.sh"))


def _run(script_name: str, workspace: Path) -> tuple[dict, int]:
    proc = subprocess.run(
        ["bash", str(CHECKS_DIR / script_name), str(workspace)],
        capture_output=True,
        text=True,
        timeout=30,
    )
    verdict = json.loads(proc.stdout)
    return verdict, proc.returncode


def _assert_verdict_shape(verdict: dict) -> None:
    assert isinstance(verdict["score"], float)
    assert isinstance(verdict["passed"], bool), (
        f"'passed' must be an explicit JSON bool, got {verdict.get('passed')!r}"
    )
    assert verdict["passed"] == (verdict["score"] >= 1.0)
    assert isinstance(verdict["message"], str) and verdict["message"]


def test_all_four_scripts_exist() -> None:
    assert SCRIPTS == [
        "check_communication.sh",
        "check_remediation.sh",
        "check_root_cause.sh",
        "check_triage.sh",
    ]


@pytest.mark.parametrize("script_name", SCRIPTS)
def test_passing_branch(script_name: str, tmp_path: Path) -> None:
    (tmp_path / "actions.jsonl").write_text(_PASSING_ACTION[script_name] + "\n")

    verdict, returncode = _run(script_name, tmp_path)

    _assert_verdict_shape(verdict)
    assert verdict["score"] == 1.0
    assert verdict["passed"] is True
    assert returncode == 0


@pytest.mark.parametrize("script_name", SCRIPTS)
def test_failing_branch(script_name: str, tmp_path: Path) -> None:
    (tmp_path / "actions.jsonl").write_text('{"action": "noop"}\n')

    verdict, returncode = _run(script_name, tmp_path)

    _assert_verdict_shape(verdict)
    assert verdict["score"] == 0.0
    assert verdict["passed"] is False
    assert returncode == 1


@pytest.mark.parametrize("script_name", SCRIPTS)
def test_missing_actions_file_branch(script_name: str, tmp_path: Path) -> None:
    verdict, returncode = _run(script_name, tmp_path)

    _assert_verdict_shape(verdict)
    assert verdict["score"] == 0.0
    assert verdict["passed"] is False
    assert returncode == 1
