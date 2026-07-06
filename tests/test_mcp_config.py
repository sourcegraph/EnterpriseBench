"""Tests for MCP configuration in run_task.py.

Verifies:
  - Correct Sourcegraph endpoint URL
  - Authorization headers present in MCP config files
  - NODE_TLS_REJECT_UNAUTHORIZED=0 set for MCP modes
  - SOURCEGRAPH_ACCESS_TOKEN passed for MCP modes
  - No MCP env vars set for baseline mode
  - HTTP-level endpoint verification
  - Config written to both project and user level
"""

from __future__ import annotations

import contextlib
import json
import os
import sys
from pathlib import Path
from unittest.mock import MagicMock, call, patch

import pytest

# Make scripts importable
sys.path.insert(
    0, str(Path(__file__).resolve().parent.parent / "scripts" / "orchestration")
)
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts" / "infra"))

from run_task import (
    RUN_STATUS_INVALID,
    SOURCEGRAPH_MCP_ENDPOINT,
    _DEFAULT_MCP_URL,
    _configure_mcp,
    _verify_mcp_endpoint,
    run_task,
    TaskRunConfig,
)

# ---------------------------------------------------------------------------
# Endpoint URL
# ---------------------------------------------------------------------------


class TestMcpEndpointUrl:
    def test_endpoint_default_is_demo(self) -> None:
        """Default endpoint must point to demo.sourcegraph.com (matches CSB token)."""
        assert "demo.sourcegraph.com" in _DEFAULT_MCP_URL

    def test_endpoint_path_is_mcp_all(self) -> None:
        """Endpoint path must be /.api/mcp/all (not /mcp or /mcp/v1)."""
        assert SOURCEGRAPH_MCP_ENDPOINT.endswith("/.api/mcp/all")

    def test_endpoint_overridable_via_env(self) -> None:
        """SOURCEGRAPH_MCP_URL env var overrides the default endpoint."""
        import importlib
        import run_task

        with patch.dict(
            os.environ,
            {"SOURCEGRAPH_MCP_URL": "https://custom.example.com/.api/mcp/all"},
        ):
            importlib.reload(run_task)
            assert (
                run_task.SOURCEGRAPH_MCP_ENDPOINT
                == "https://custom.example.com/.api/mcp/all"
            )
        # Restore
        importlib.reload(run_task)


# ---------------------------------------------------------------------------
# Auth headers in _configure_mcp
# ---------------------------------------------------------------------------


class TestMcpAuthHeaders:
    """Verify that _configure_mcp writes .mcp.json with Authorization header."""

    def _capture_mcp_config(self, mode: str = "mcp_only") -> dict:
        """Call _configure_mcp with mocks and return the parsed project-level config.

        The new implementation uses docker cp to write config files. We capture
        the content written to the temp file that gets docker-cp'd into the
        container at /workspace/.mcp.json.
        """
        # Read file contents at docker cp time (before finally block deletes temps)
        captured_configs: list[tuple[str, str]] = []  # (dest, content)

        def mock_docker_exec(
            container_id: str,
            cmd: list[str],
            timeout: int = 120,
            workdir: str = "/workspace",
        ):
            return MagicMock(returncode=0, stdout="", stderr="")

        def mock_docker_cp(src: str, dest: str):
            with open(src) as f:
                captured_configs.append((dest, f.read()))

        def mock_verify_endpoint(container_id: str, sg_token: str):
            return True

        def mock_mcp_exec(container_id: str, cmd: list, timeout: int = 30):
            return MagicMock(
                returncode=0,
                stdout="sourcegraph  Connected",
                stderr="",
            )

        with (
            patch("run_task._docker_exec", side_effect=mock_docker_exec),
            patch("run_task._docker_cp", side_effect=mock_docker_cp),
            patch("run_task._verify_mcp_endpoint", side_effect=mock_verify_endpoint),
            patch("run_task._mcp_exec", side_effect=mock_mcp_exec),
            patch.dict(os.environ, {"SOURCEGRAPH_ACCESS_TOKEN": "sgp_test_token_123"}),
        ):
            _configure_mcp("test-container", mode)

        # Find the docker cp call for /workspace/.mcp.json
        for dest, content in captured_configs:
            if "/workspace/.mcp.json" in dest:
                return json.loads(content)
        pytest.fail("No docker cp to /workspace/.mcp.json found")

    def test_mcp_config_includes_authorization_header(self) -> None:
        config = self._capture_mcp_config("mcp_only")
        sg_config = config.get("mcpServers", {}).get("sourcegraph", {})
        headers = sg_config.get("headers", {})
        assert "Authorization" in headers, ".mcp.json must include Authorization header"

    def test_mcp_config_auth_header_format(self) -> None:
        config = self._capture_mcp_config("mcp_only")
        auth = config["mcpServers"]["sourcegraph"]["headers"]["Authorization"]
        assert auth.startswith(
            "token "
        ), "Authorization header must start with 'token '"

    def test_mcp_config_uses_correct_endpoint(self) -> None:
        config = self._capture_mcp_config("mcp_only")
        url = config["mcpServers"]["sourcegraph"]["url"]
        assert url == SOURCEGRAPH_MCP_ENDPOINT

    def test_mcp_config_type_is_http(self) -> None:
        config = self._capture_mcp_config("mcp_only")
        assert config["mcpServers"]["sourcegraph"]["type"] == "http"

    def test_hybrid_mode_also_configures_mcp(self) -> None:
        config = self._capture_mcp_config("hybrid")
        assert "sourcegraph" in config.get("mcpServers", {})
        assert "Authorization" in config["mcpServers"]["sourcegraph"].get("headers", {})


# ---------------------------------------------------------------------------
# Environment variables for MCP modes
# ---------------------------------------------------------------------------


class TestMcpEnvVars:
    """Verify NODE_TLS_REJECT_UNAUTHORIZED and SOURCEGRAPH_ACCESS_TOKEN for MCP modes."""

    def _get_env_extra_for_mode(self, mode: str) -> dict[str, str]:
        """Extract env_extra that would be passed to _run_agent for the given mode.

        We patch everything heavy (parse, build, docker ops) and capture what
        env_extra is passed to _run_agent.
        """
        captured_env: dict[str, str] = {}

        def mock_run_agent(
            container_id, agent_command, timeout, output_dir, env_extra=None
        ):
            captured_env.update(env_extra or {})
            return (0, 10.0)

        fake_task_data = {
            "task": {
                "id": "test-mcp-env-001",
                "suite": "test",
                "task_type": "test",
                "difficulty": "easy",
                "session_type": "single",
            },
            "repos": [],
        }

        config = TaskRunConfig(
            task_toml=Path("/fake/task.toml"),
            agent_command="claude -p",
            timeout=300,
            mode=mode,
        )

        with patch("run_task._parse_task", return_value=fake_task_data), patch(
            "run_task._generate_dockerfile", return_value=Path("/fake/Dockerfile")
        ), patch("run_task._docker_build"), patch(
            "run_task._docker_create_container", return_value="fake-container-id"
        ), patch(
            "run_task._docker_start"
        ), patch(
            "run_task._setup_container", return_value=({}, set())
        ), patch(
            "run_task._run_health_check", return_value=True
        ), patch(
            "run_task._configure_mcp"
        ), patch(
            "run_task._refresh_and_verify_grading_files", return_value=[]
        ), patch(
            "run_task._run_agent", side_effect=mock_run_agent
        ) as mock_agent, patch(
            "run_task._run_scoring", return_value={"task_score": 0.0}
        ), patch(
            "run_task._save_results"
        ), patch(
            "run_task._extract_tool_usage", return_value={}
        ), patch(
            "run_task._copy_agent_trace", return_value=False
        ), patch(
            "run_task._check_disk_space", return_value=True
        ), patch(
            "run_task._docker_stop_rm"
        ), patch.dict(
            os.environ, {"SOURCEGRAPH_ACCESS_TOKEN": "sgp_test_token_xyz"}
        ):
            run_task(config)

        return captured_env

    def test_mcp_only_sets_node_tls_reject(self) -> None:
        env = self._get_env_extra_for_mode("mcp_only")
        assert env.get("NODE_TLS_REJECT_UNAUTHORIZED") == "0"

    def test_hybrid_sets_node_tls_reject(self) -> None:
        env = self._get_env_extra_for_mode("hybrid")
        assert env.get("NODE_TLS_REJECT_UNAUTHORIZED") == "0"

    def test_baseline_does_not_set_node_tls_reject(self) -> None:
        env = self._get_env_extra_for_mode("baseline")
        assert "NODE_TLS_REJECT_UNAUTHORIZED" not in env

    def test_mcp_only_passes_sourcegraph_token(self) -> None:
        env = self._get_env_extra_for_mode("mcp_only")
        assert env.get("SOURCEGRAPH_ACCESS_TOKEN") == "sgp_test_token_xyz"

    def test_hybrid_passes_sourcegraph_token(self) -> None:
        env = self._get_env_extra_for_mode("hybrid")
        assert env.get("SOURCEGRAPH_ACCESS_TOKEN") == "sgp_test_token_xyz"

    def test_baseline_does_not_pass_sourcegraph_token(self) -> None:
        env = self._get_env_extra_for_mode("baseline")
        assert "SOURCEGRAPH_ACCESS_TOKEN" not in env


# ---------------------------------------------------------------------------
# _configure_mcp skips baseline
# ---------------------------------------------------------------------------


class TestMcpSkipsBaseline:
    def test_baseline_mode_skips_mcp_config(self) -> None:
        """_configure_mcp should be a no-op for baseline mode."""
        mock_exec = MagicMock()
        with patch("run_task._docker_exec", mock_exec):
            _configure_mcp("test-container", "baseline")
        mock_exec.assert_not_called()


# ---------------------------------------------------------------------------
# Hard-fail on MCP pre-flight failure (bead EnterpriseBench-c7wb)
# ---------------------------------------------------------------------------


class TestMcpPreflightHardFail:
    """A failed MCP pre-flight (401 / unreachable) on an MCP arm must hard-fail.

    Regression for bead EnterpriseBench-c7wb: run_task.py used to log
    "agent will run but MCP may not work" and proceed, recording a degraded
    no-MCP run as if it were a real MCP measurement. The MCP arm must instead
    be routed to the infra-error re-run channel, and the agent must never run.
    The baseline arm (no MCP) must stay completely unaffected.
    """

    @staticmethod
    def _fake_task_data() -> dict:
        return {
            "task": {
                "id": "test-mcp-preflight-001",
                "suite": "test",
                "task_type": "test",
                "difficulty": "easy",
                "session_type": "single",
            },
            "repos": [],
        }

    def _run_with_failed_preflight(self, mode: str):
        """Drive run_task() through a simulated 401/unreachable MCP endpoint.

        `_verify_mcp_endpoint` is stubbed to return False (the real
        `_configure_mcp` runs and returns False on that pre-flight failure).
        Returns (result, run_agent_mock) so callers can assert the run was
        classified as an infra error AND that the agent never executed.
        """
        agent_mock = MagicMock(return_value=(0, 10.0))

        config = TaskRunConfig(
            task_toml=Path("/fake/task.toml"),
            agent_command="claude -p",
            timeout=300,
            mode=mode,
        )

        # ExitStack, not a chained `with A, B, C, ...:` — this many patches
        # chained in one `with` statement sits right at CPython's
        # statically-nested-block compile limit, and nesting a second `with`
        # inside the first doesn't help (the depth is cumulative).
        with contextlib.ExitStack() as stack:
            stack.enter_context(
                patch("run_task._parse_task", return_value=self._fake_task_data())
            )
            stack.enter_context(
                patch("run_task._generate_dockerfile", return_value=Path("/fake/Dockerfile"))
            )
            stack.enter_context(patch("run_task._docker_build"))
            stack.enter_context(
                patch("run_task._docker_create_container", return_value="fake-container-id")
            )
            stack.enter_context(patch("run_task._docker_start"))
            stack.enter_context(
                patch("run_task._setup_container", return_value=({}, set()))
            )
            stack.enter_context(patch("run_task._run_health_check", return_value=True))
            # Simulate a 401 / unreachable endpoint: HTTP pre-flight fails.
            stack.enter_context(patch("run_task._verify_mcp_endpoint", return_value=False))
            stack.enter_context(patch("run_task._docker_cp"))
            stack.enter_context(
                patch(
                    "run_task._docker_exec",
                    return_value=MagicMock(returncode=0, stdout="", stderr=""),
                )
            )
            stack.enter_context(
                patch(
                    "run_task._mcp_exec",
                    return_value=MagicMock(returncode=0, stdout="", stderr=""),
                )
            )
            stack.enter_context(patch("run_task._run_agent", side_effect=agent_mock))
            stack.enter_context(patch("run_task._refresh_and_verify_grading_files", return_value=[]))
            stack.enter_context(patch("run_task._run_scoring", return_value={"task_score": 0.0}))
            stack.enter_context(patch("run_task._save_results"))
            stack.enter_context(patch("run_task._extract_tool_usage", return_value={}))
            stack.enter_context(patch("run_task._copy_agent_trace", return_value=False))
            stack.enter_context(patch("run_task._check_disk_space", return_value=True))
            stack.enter_context(patch("run_task._docker_stop_rm"))
            stack.enter_context(patch("run_task.time.sleep"))
            stack.enter_context(
                patch.dict(os.environ, {"SOURCEGRAPH_ACCESS_TOKEN": "sgp_expired_token"})
            )

            result = run_task(config)

        return result, agent_mock

    def test_mcp_only_preflight_failure_is_infra_error(self) -> None:
        result, _ = self._run_with_failed_preflight("mcp_only")
        assert result.phase == "mcp_infra_error"
        assert result.failure_class == "infra_mcp_preflight"

    def test_mcp_only_preflight_failure_is_not_a_scored_success(self) -> None:
        """The degraded run must NOT be recorded as a real scored run."""
        result, _ = self._run_with_failed_preflight("mcp_only")
        assert result.success is False
        assert result.status == RUN_STATUS_INVALID

    def test_mcp_only_preflight_failure_does_not_run_agent(self) -> None:
        """Hard-fail must short-circuit BEFORE the agent runs (no degraded run)."""
        _, agent_mock = self._run_with_failed_preflight("mcp_only")
        agent_mock.assert_not_called()

    def test_hybrid_preflight_failure_is_infra_error(self) -> None:
        """The hybrid arm is also MCP-bearing and must hard-fail identically."""
        result, agent_mock = self._run_with_failed_preflight("hybrid")
        assert result.phase == "mcp_infra_error"
        assert result.success is False
        agent_mock.assert_not_called()

    def test_baseline_arm_unaffected_by_mcp_preflight(self) -> None:
        """Baseline has no MCP: it must run the agent and never see a pre-flight."""
        agent_mock = MagicMock(return_value=(0, 10.0))
        verify_mock = MagicMock(return_value=False)

        config = TaskRunConfig(
            task_toml=Path("/fake/task.toml"),
            agent_command="claude -p",
            timeout=300,
            mode="baseline",
        )

        with patch("run_task._parse_task", return_value=self._fake_task_data()), patch(
            "run_task._generate_dockerfile", return_value=Path("/fake/Dockerfile")
        ), patch("run_task._docker_build"), patch(
            "run_task._docker_create_container", return_value="fake-container-id"
        ), patch("run_task._docker_start"), patch(
            "run_task._setup_container", return_value=({}, set())
        ), patch(
            "run_task._run_health_check", return_value=True
        ), patch(
            "run_task._verify_mcp_endpoint", side_effect=verify_mock
        ), patch(
            "run_task._chown_to_agent"
        ), patch(
            "run_task._assert_agent_readable", return_value=(True, "")
        ), patch(
            "run_task._run_agent", side_effect=agent_mock
        ), patch(
            "run_task._refresh_and_verify_grading_files", return_value=[]
        ), patch(
            "run_task._run_scoring", return_value={"task_score": 1.0}
        ), patch("run_task._save_results"), patch(
            "run_task._extract_tool_usage", return_value={"num_turns": 5}
        ), patch(
            "run_task._scan_mcp_config_error", return_value=False
        ), patch(
            "run_task._copy_agent_trace", return_value=False
        ), patch(
            "run_task._check_disk_space", return_value=True
        ), patch("run_task._docker_stop_rm"):
            result = run_task(config)

        verify_mock.assert_not_called()  # baseline never reaches MCP pre-flight
        agent_mock.assert_called_once()  # baseline runs normally
        assert result.phase == "complete"
        assert result.success is True


# ---------------------------------------------------------------------------
# HTTP-level endpoint verification
# ---------------------------------------------------------------------------


class TestVerifyMcpEndpoint:
    """Verify the direct HTTP endpoint check for reliable MCP auth."""

    def test_returns_true_on_success(self) -> None:
        """Successful curl (rc=0) should return True."""
        mock_result = MagicMock(returncode=0, stdout="200", stderr="")

        with patch("run_task._docker_exec", return_value=mock_result):
            assert _verify_mcp_endpoint("test-container", "sgp_token") is True

    def test_retries_on_failure(self) -> None:
        """Should retry up to 5 times on failure."""
        fail = MagicMock(returncode=1, stdout="000", stderr="Connection refused")
        success = MagicMock(returncode=0, stdout="200", stderr="")

        with patch("run_task._docker_exec", side_effect=[fail, fail, success]):
            with patch("run_task.time.sleep"):  # skip actual sleep
                assert _verify_mcp_endpoint("test-container", "sgp_token") is True

    def test_returns_false_after_max_retries(self) -> None:
        """Should return False after exhausting retries."""
        fail = MagicMock(returncode=1, stdout="401", stderr="Unauthorized")

        with patch("run_task._docker_exec", return_value=fail):
            with patch("run_task.time.sleep"):
                assert _verify_mcp_endpoint("test-container", "sgp_token") is False

    def test_uses_curl_with_auth_header(self) -> None:
        """Curl command should include Authorization header."""
        captured_cmds: list[list[str]] = []

        def capture_exec(container_id, cmd, timeout=120, workdir="/workspace"):
            captured_cmds.append(cmd)
            return MagicMock(returncode=0, stdout="200", stderr="")

        with patch("run_task._docker_exec", side_effect=capture_exec):
            _verify_mcp_endpoint("test-container", "sgp_my_token")

        assert len(captured_cmds) >= 1
        curl_cmd = captured_cmds[0]
        assert "curl" in curl_cmd
        assert "Authorization: token sgp_my_token" in " ".join(curl_cmd)
        assert "-k" in curl_cmd  # TLS skip flag


# ---------------------------------------------------------------------------
# Dual config file writing
# ---------------------------------------------------------------------------


class TestMcpDualConfig:
    """Verify config is written to both project and user level."""

    def test_writes_to_both_project_and_user_dirs(self) -> None:
        """_configure_mcp should docker cp to both /workspace and /home/agent/.claude."""
        cp_dests: list[str] = []

        def mock_docker_cp(src: str, dest: str):
            cp_dests.append(dest)

        def mock_docker_exec(container_id, cmd, timeout=120, workdir="/workspace"):
            return MagicMock(returncode=0, stdout="", stderr="")

        def mock_mcp_exec(container_id, cmd, timeout=30):
            return MagicMock(
                returncode=0,
                stdout="sourcegraph  Connected",
                stderr="",
            )

        with (
            patch("run_task._docker_exec", side_effect=mock_docker_exec),
            patch("run_task._docker_cp", side_effect=mock_docker_cp),
            patch("run_task._verify_mcp_endpoint", return_value=True),
            patch("run_task._mcp_exec", side_effect=mock_mcp_exec),
            patch.dict(os.environ, {"SOURCEGRAPH_ACCESS_TOKEN": "sgp_test"}),
        ):
            _configure_mcp("test-container", "mcp_only")

        workspace_writes = [d for d in cp_dests if "/workspace/" in d]
        user_writes = [d for d in cp_dests if "/home/agent/.claude/" in d]
        assert (
            len(workspace_writes) == 1
        ), f"Expected 1 workspace write, got {workspace_writes}"
        assert len(user_writes) == 1, f"Expected 1 user-level write, got {user_writes}"
