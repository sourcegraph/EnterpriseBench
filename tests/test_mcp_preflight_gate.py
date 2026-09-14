"""Tests for the MCP pre-flight hard gate in run_task.py (bead EnterpriseBench-vl0k).

The mcp_only/hybrid arms must never run degraded: _configure_mcp reports
whether the handshake succeeded, and the caller routes a failed pre-flight
to the infra-error re-run channel with status=RUN_STATUS_INVALID instead of
scoring the run. Prior to the fix, _configure_mcp returned None, the call
site read an undefined mcp_handshake_ok, and RUN_STATUS_INVALID /
TaskRunResult.status did not exist — any MCP-mode run crashed with
NameError at the gate.
"""

from __future__ import annotations

import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

# Make scripts importable (same convention as test_infra_error_classification)
sys.path.insert(
    0, str(Path(__file__).resolve().parent.parent / "scripts" / "orchestration")
)

import run_task
from run_task import RUN_STATUS_INVALID, TaskRunResult


class TestGateSymbols:
    def test_run_status_invalid_constant_exists(self):
        assert RUN_STATUS_INVALID == "invalid"

    def test_task_run_result_has_status_field(self):
        result = TaskRunResult(task_id="t")
        assert result.status == ""
        result.status = RUN_STATUS_INVALID
        assert result.status == "invalid"


class TestConfigureMcpReturn:
    def test_non_mcp_mode_returns_true(self):
        # baseline has no MCP; the gate must not fire.
        assert run_task._configure_mcp("cid", "baseline") is True

    def test_endpoint_unreachable_returns_false_without_writing_config(self):
        docker_cp = MagicMock()
        with (
            patch.dict("os.environ", {"SOURCEGRAPH_ACCESS_TOKEN": "tok"}),
            patch.object(run_task, "_verify_mcp_endpoint", return_value=False),
            patch.object(run_task, "_docker_cp", docker_cp),
        ):
            assert run_task._configure_mcp("cid", "mcp_only") is False
        docker_cp.assert_not_called()

    def test_handshake_connected_returns_true(self):
        connected = MagicMock(stdout="sourcegraph: https://... - ✓ Connected")
        # _trust_project_mcp_servers (EnterpriseBench-rryas.4) runs before the
        # handshake; it has its own tests, so mock it True here to keep this test
        # focused on the connected-handshake contract rather than trust-file I/O.
        with (
            patch.dict("os.environ", {"SOURCEGRAPH_ACCESS_TOKEN": "tok"}),
            patch.object(run_task, "_verify_mcp_endpoint", return_value=True),
            patch.object(run_task, "_docker_cp", MagicMock()),
            patch.object(
                run_task,
                "_docker_exec",
                MagicMock(return_value=MagicMock(returncode=0, stdout="", stderr="")),
            ),
            patch.object(run_task, "_trust_project_mcp_servers", return_value=True),
            patch.object(run_task, "_mcp_exec", return_value=connected),
        ):
            assert run_task._configure_mcp("cid", "mcp_only") is True

    def test_handshake_never_connects_returns_false(self):
        needs_auth = MagicMock(stdout="sourcegraph: https://... - needs-auth")
        # Trust succeeds so the False return is unambiguously the never-connecting
        # handshake, not the trust step failing under the bare _docker_exec mock.
        with (
            patch.dict("os.environ", {"SOURCEGRAPH_ACCESS_TOKEN": "tok"}),
            patch.object(run_task, "_verify_mcp_endpoint", return_value=True),
            patch.object(run_task, "_docker_cp", MagicMock()),
            patch.object(
                run_task,
                "_docker_exec",
                MagicMock(return_value=MagicMock(returncode=0, stdout="", stderr="")),
            ),
            patch.object(run_task, "_trust_project_mcp_servers", return_value=True),
            patch.object(run_task, "_mcp_exec", return_value=needs_auth),
            patch.object(run_task.time, "sleep", MagicMock()),
        ):
            assert run_task._configure_mcp("cid", "mcp_only") is False
