"""Regression tests for the p6ux.19 calibration gold corrections.

These guard against the ground-truth bugs that scored correct agent answers 0.0:

1. cal-err-requests-proxy-003 (customer_escalation/calibration-003): required_files
   pointed at utils.py / models.py, but Proxy-Authorization is stripped in
   requests/sessions.py rebuild_proxies (verified against psf/requests v2.32.3).

2. cal-drift-click-envvar-002 (platform_engineering/calibration-002): drift gold used
   the invented key "envvar_resolution"; real Click identifiers are
   resolve_envvar_value / consume_value (verified against pallets/click 8.2.1).

3. cal-drift-flask-config-001 / cal-drift-click-envvar-002 expected_values: gold
   required whole prose sentences verbatim ("Default value should be returned when
   envvar is unset"), which a correct answer never reproduces — relaxed to real
   identifiers a correct answer contains.
"""

from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).parent.parent
BENCHMARKS = ROOT / "benchmarks"

REQUESTS_DIR = BENCHMARKS / "customer_escalation" / "calibration-003"
CLICK_DIR = BENCHMARKS / "platform_engineering" / "calibration-002"
FLASK_DIR = BENCHMARKS / "platform_engineering" / "calibration-001"


def _run(task_dir: Path, script: str, answer: dict) -> dict:
    workspace = task_dir  # only WORKSPACE/agent_output and TASK_DIR matter
    with _temp_workspace(answer) as ws:
        env = os.environ.copy()
        env["WORKSPACE"] = str(ws)
        env["TASK_DIR"] = str(task_dir)
        env["TASK_ID"] = task_dir.name
        result = subprocess.run(
            ["bash", str(task_dir / "checks" / script)],
            capture_output=True, text=True, timeout=30, env=env,
        )
    return json.loads(result.stdout.strip())


class _temp_workspace:
    def __init__(self, answer: dict) -> None:
        self._answer = answer
        self._tmp: Path | None = None

    def __enter__(self) -> Path:
        import tempfile

        self._dir = tempfile.TemporaryDirectory()
        ws = Path(self._dir.name)
        (ws / "agent_output").mkdir(parents=True)
        (ws / "agent_output" / "answer.json").write_text(json.dumps(self._answer))
        return ws

    def __exit__(self, *exc: object) -> None:
        self._dir.cleanup()


# -- 1. requests proxy gold points at the real stripping site -----------------

class TestRequestsProxyGold:
    def test_required_file_is_sessions(self) -> None:
        gt = json.loads((REQUESTS_DIR / "ground_truth.json").read_text())
        required = {f["path"] for f in gt["required_files"]}
        assert required == {"src/requests/sessions.py"}, (
            f"required_files must be sessions.py (rebuild_proxies), got {required}"
        )

    def test_agent_naming_sessions_scores_full(self) -> None:
        result = _run(
            REQUESTS_DIR, "check_error_source.sh",
            {"files": [{"path": "src/requests/sessions.py"}]},
        )
        assert result["score"] == 1.0 and result["passed"] is True

    def test_old_wrong_files_score_zero(self) -> None:
        result = _run(
            REQUESTS_DIR, "check_error_source.sh",
            {"files": [{"path": "src/requests/utils.py"},
                       {"path": "src/requests/models.py"}]},
        )
        assert result["score"] == 0.0


# -- 2 & 3. click drift gold uses real identifiers, not invented keys/prose ----

class TestClickDriftGold:
    def test_no_invented_keys(self) -> None:
        gt = json.loads((CLICK_DIR / "ground_truth.json").read_text())
        keys = {dp["key"] for dp in gt["drift_points"]}
        assert "envvar_resolution" not in keys, "invented key still present"
        assert keys == {"resolve_envvar_value", "consume_value"}, keys

    def test_expected_values_are_short_identifiers(self) -> None:
        gt = json.loads((CLICK_DIR / "ground_truth.json").read_text())
        for dp in gt["drift_points"]:
            # Real identifier, not a multi-word prose sentence.
            assert " " not in dp["expected"], (
                f"expected should be a checkable identifier, got prose: {dp['expected']!r}"
            )

    def test_correct_answer_passes_both_checks(self) -> None:
        answer = {"drift_points": [
            {"key": "resolve_envvar_value",
             "expected": "falls back to get_default", "actual": "returns None"},
            {"key": "consume_value",
             "expected": "passes default to type_cast_value", "actual": "passes None"},
        ]}
        dp = _run(CLICK_DIR, "check_drift_points.sh", answer)
        ev = _run(CLICK_DIR, "check_expected_values.sh", answer)
        assert dp["passed"] is True and ev["passed"] is True, (dp, ev)


# -- flask drift gold: real config keys, relaxed expected ----------------------

class TestFlaskDriftGold:
    def test_expected_values_are_short_identifiers(self) -> None:
        gt = json.loads((FLASK_DIR / "ground_truth.json").read_text())
        for dp in gt["drift_points"]:
            assert " " not in dp["expected"], (
                f"expected should be a checkable identifier, got prose: {dp['expected']!r}"
            )

    def test_correct_answer_passes_both_checks(self) -> None:
        answer = {"drift_points": [
            {"key": "SECRET_KEY",
             "expected": "from_envvar should override from_object", "actual": "persists"},
            {"key": "SESSION_COOKIE_SECURE",
             "expected": "from_envvar loads a bool as string", "actual": "not overriding"},
        ]}
        dp = _run(FLASK_DIR, "check_drift_points.sh", answer)
        ev = _run(FLASK_DIR, "check_expected_values.sh", answer)
        assert dp["passed"] is True and ev["passed"] is True, (dp, ev)
