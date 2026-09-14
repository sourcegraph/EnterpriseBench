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
    _assert_agent_readable,
    _chown_to_agent,
    _configure_mcp,
    _scan_mcp_config_error,
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
        """SOURCEGRAPH_MCP_URL env var overrides the default endpoint.

        rryas.2 made the repo's ``.env.local`` authoritative for SOURCEGRAPH_*
        keys, and that file defines SOURCEGRAPH_MCP_URL — so on a plain reload it
        would clobber the ambient override we set here. Suppress ONLY the
        ``.env.local`` read (a targeted ``Path.is_file`` shim that defers every
        other import-time check) to isolate the ambient-env → endpoint
        derivation; the loader's own precedence is covered by
        TestLoadEnvLocalPrecedence in test_run_task_env_and_mcp_trust.
        """
        import importlib
        from pathlib import Path

        import run_task

        real_is_file = Path.is_file

        def is_file_without_env_local(self: Path) -> bool:
            if self.name == ".env.local":
                return False
            return real_is_file(self)

        with (
            patch.object(Path, "is_file", is_file_without_env_local),
            patch.dict(
                os.environ,
                {"SOURCEGRAPH_MCP_URL": "https://custom.example.com/.api/mcp/all"},
            ),
        ):
            importlib.reload(run_task)
            assert (
                run_task.SOURCEGRAPH_MCP_ENDPOINT
                == "https://custom.example.com/.api/mcp/all"
            )
        # Restore the module to its real, .env.local-derived state.
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
            user: str | None = None,
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
            "run_task._setup_container"
        ), patch(
            "run_task._run_health_check", return_value=True
        ), patch(
            "run_task._configure_mcp"
        ), patch(
            "run_task._chown_to_agent"
        ), patch(
            "run_task._assert_agent_readable", return_value=(True, "")
        ), patch(
            "run_task._scan_mcp_config_error", return_value=False
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

        with patch("run_task._parse_task", return_value=self._fake_task_data()), patch(
            "run_task._generate_dockerfile", return_value=Path("/fake/Dockerfile")
        ), patch("run_task._docker_build"), patch(
            "run_task._docker_create_container", return_value="fake-container-id"
        ), patch("run_task._docker_start"), patch("run_task._setup_container"), patch(
            "run_task._run_health_check", return_value=True
        ), patch(
            # Simulate a 401 / unreachable endpoint: HTTP pre-flight fails.
            "run_task._verify_mcp_endpoint",
            return_value=False,
        ), patch(
            "run_task._docker_cp"
        ), patch(
            "run_task._docker_exec",
            return_value=MagicMock(returncode=0, stdout="", stderr=""),
        ), patch(
            "run_task._mcp_exec",
            return_value=MagicMock(returncode=0, stdout="", stderr=""),
        ), patch(
            "run_task._run_agent", side_effect=agent_mock
        ), patch(
            "run_task._run_scoring", return_value={"task_score": 0.0}
        ), patch("run_task._save_results"), patch(
            "run_task._extract_tool_usage", return_value={}
        ), patch(
            "run_task._copy_agent_trace", return_value=False
        ), patch(
            "run_task._check_disk_space", return_value=True
        ), patch("run_task._docker_stop_rm"), patch(
            "run_task.time.sleep"
        ), patch.dict(
            os.environ, {"SOURCEGRAPH_ACCESS_TOKEN": "sgp_expired_token"}
        ):
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
        ), patch("run_task._docker_start"), patch("run_task._setup_container"), patch(
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


class TestPreAgentReadabilityGate:
    """The pre-agent gate branches actually wired into run_task().

    The helpers themselves are unit-tested below; these tests drive run_task()
    end-to-end (with docker mocked) to verify the wiring: an unreadable
    instruction/config file must invalidate the run BEFORE the agent starts,
    and an MCP-config error in agent stderr must invalidate it after
    (bead EnterpriseBench-0l2a / s58f).
    """

    @staticmethod
    def _fake_task_data() -> dict:
        return {
            "task": {
                "id": "test-readability-gate-001",
                "suite": "test",
                "task_type": "test",
                "difficulty": "easy",
                "session_type": "single",
            },
            "repos": [],
        }

    def _run(
        self,
        mode: str = "baseline",
        readable: tuple[bool, str] = (True, ""),
        mcp_config_error: bool = False,
    ):
        agent_mock = MagicMock(return_value=(0, 10.0))
        readable_mock = MagicMock(return_value=readable)

        config = TaskRunConfig(
            task_toml=Path("/fake/task.toml"),
            agent_command="claude -p",
            timeout=300,
            mode=mode,
        )

        with patch("run_task._parse_task", return_value=self._fake_task_data()), patch(
            "run_task._generate_dockerfile", return_value=Path("/fake/Dockerfile")
        ), patch("run_task._docker_build"), patch(
            "run_task._docker_create_container", return_value="fake-container-id"
        ), patch("run_task._docker_start"), patch("run_task._setup_container"), patch(
            "run_task._run_health_check", return_value=True
        ), patch(
            "run_task._configure_mcp"
        ), patch(
            "run_task._chown_to_agent"
        ), patch(
            "run_task._assert_agent_readable", side_effect=readable_mock
        ), patch(
            "run_task._scan_mcp_config_error", return_value=mcp_config_error
        ), patch(
            "run_task._run_agent", side_effect=agent_mock
        ), patch(
            "run_task._run_scoring", return_value={"task_score": 0.0}
        ), patch("run_task._save_results"), patch(
            "run_task._extract_tool_usage", return_value={}
        ), patch(
            "run_task._copy_agent_trace", return_value=False
        ), patch(
            "run_task._check_disk_space", return_value=True
        ), patch("run_task._docker_stop_rm"), patch.dict(
            os.environ, {"SOURCEGRAPH_ACCESS_TOKEN": "sgp_test_token_xyz"}
        ):
            result = run_task(config)

        return result, agent_mock, readable_mock

    def test_unreadable_file_invalidates_run_before_agent(self) -> None:
        result, agent_mock, _ = self._run(
            readable=(False, "agent user cannot read /workspace/instruction.md")
        )
        assert result.status == RUN_STATUS_INVALID
        assert result.phase == "agent_preflight_failed"
        assert result.failure_class == "infra_perms"
        assert result.success is False
        agent_mock.assert_not_called()

    def test_mcp_config_error_in_stderr_invalidates_run_after_agent(self) -> None:
        result, agent_mock, _ = self._run(mode="mcp_only", mcp_config_error=True)
        agent_mock.assert_called_once()
        assert result.status == RUN_STATUS_INVALID
        assert result.failure_class == "infra_mcp_config"
        assert result.phase == "agent_infra_error"
        assert result.success is False

    def test_baseline_readability_targets_are_instruction_only(self) -> None:
        _, _, readable_mock = self._run(mode="baseline")
        readable_mock.assert_called_once()
        assert readable_mock.call_args.args[1] == ["/workspace/instruction.md"]

    def test_mcp_mode_readability_targets_include_mcp_configs(self) -> None:
        _, _, readable_mock = self._run(mode="mcp_only")
        readable_mock.assert_called_once()
        assert readable_mock.call_args.args[1] == [
            "/workspace/instruction.md",
            "/workspace/.mcp.json",
            "/home/agent/.mcp.json",
        ]


# ---------------------------------------------------------------------------
# HTTP-level endpoint verification
# ---------------------------------------------------------------------------


class TestVerifyMcpEndpoint:
    """Verify the direct HTTP endpoint check for reliable MCP auth.

    The token is staged in a 0600 in-container file and read by curl via
    ``-H @file`` — it must never appear in argv (EnterpriseBench-rryas.5). Each
    test patches ``_docker_cp`` (the header stager) and routes ``_docker_exec``
    by command so chmod / curl / rm are handled independently of curl retries.
    """

    @staticmethod
    def _exec_router(curl_results):
        """A _docker_exec stand-in: curl calls pull from ``curl_results`` (an
        iterator of CompletedProcess-likes); id / chown / chmod / rm return
        benign success. ``user`` is accepted because the staging of the auth
        header runs its ownership fixups as root."""
        it = iter(curl_results)

        def _exec(container_id, cmd, timeout=120, workdir="/workspace", user=None):
            if cmd and cmd[0] == "curl":
                return next(it)
            return MagicMock(returncode=0, stdout="", stderr="")

        return _exec

    def test_returns_true_on_success(self) -> None:
        """Successful curl (rc=0) should return True."""
        ok = MagicMock(returncode=0, stdout="200", stderr="")

        with (
            patch("run_task._docker_cp", MagicMock()),
            patch("run_task._docker_exec", side_effect=self._exec_router([ok])),
        ):
            assert _verify_mcp_endpoint("test-container", "sgp_token") is True

    def test_retries_on_failure(self) -> None:
        """Should retry on failure, then succeed."""
        fail = MagicMock(returncode=1, stdout="000", stderr="Connection refused")
        success = MagicMock(returncode=0, stdout="200", stderr="")

        with (
            patch("run_task._docker_cp", MagicMock()),
            patch(
                "run_task._docker_exec",
                side_effect=self._exec_router([fail, fail, success]),
            ),
            patch("run_task.time.sleep"),
        ):
            assert _verify_mcp_endpoint("test-container", "sgp_token") is True

    def test_returns_false_after_max_retries(self) -> None:
        """Should return False after exhausting retries."""
        fail = MagicMock(returncode=1, stdout="401", stderr="Unauthorized")

        with (
            patch("run_task._docker_cp", MagicMock()),
            patch(
                "run_task._docker_exec",
                side_effect=self._exec_router([fail] * 5),
            ),
            patch("run_task.time.sleep"),
        ):
            assert _verify_mcp_endpoint("test-container", "sgp_token") is False

    def test_token_never_appears_in_argv(self) -> None:
        """rryas.5: the raw token must not appear in ANY docker exec argv; the
        auth header is read from a file via ``-H @<path>``."""
        captured_cmds: list[list[str]] = []
        header_writes: list[tuple[str, str]] = []

        def capture_exec(container_id, cmd, timeout=120, workdir="/workspace", user=None):
            captured_cmds.append(cmd)
            if cmd and cmd[0] == "curl":
                return MagicMock(returncode=0, stdout="200", stderr="")
            return MagicMock(returncode=0, stdout="", stderr="")

        def capture_cp(src, dest):
            with open(src) as f:
                header_writes.append((dest, f.read()))

        with (
            patch("run_task._docker_cp", side_effect=capture_cp),
            patch("run_task._docker_exec", side_effect=capture_exec),
        ):
            _verify_mcp_endpoint("test-container", "sgp_my_token")

        # The token appears ONLY in the docker-cp'd header file, never in argv.
        assert any("sgp_my_token" in content for _, content in header_writes)
        for cmd in captured_cmds:
            assert "sgp_my_token" not in " ".join(cmd), f"token leaked into argv: {cmd}"

        curl_cmds = [c for c in captured_cmds if c and c[0] == "curl"]
        assert curl_cmds, "expected a curl invocation"
        curl_cmd = curl_cmds[0]
        # Header is read from a file: -H @<path>, and the file holds the auth line.
        file_headers = [
            curl_cmd[i + 1]
            for i, a in enumerate(curl_cmd[:-1])
            if a == "-H" and curl_cmd[i + 1].startswith("@")
        ]
        assert file_headers, f"expected -H @file, got {curl_cmd}"
        hdr_path = file_headers[0][1:]  # strip leading '@'
        assert any(dest.endswith(hdr_path) for dest, _ in header_writes)
        assert "-k" in curl_cmd  # TLS skip flag

    def test_header_file_is_chmod_600_and_removed(self) -> None:
        """The staged header must be locked down (0600) and scrubbed afterward."""
        cmds: list[list[str]] = []

        def capture_exec(container_id, cmd, timeout=120, workdir="/workspace", user=None):
            cmds.append(cmd)
            if cmd and cmd[0] == "curl":
                return MagicMock(returncode=0, stdout="200", stderr="")
            return MagicMock(returncode=0, stdout="", stderr="")

        with (
            patch("run_task._docker_cp", MagicMock()),
            patch("run_task._docker_exec", side_effect=capture_exec),
        ):
            _verify_mcp_endpoint("test-container", "sgp_token")

        chmods = [c for c in cmds if c[:2] == ["chmod", "600"]]
        rms = [c for c in cmds if c[:2] == ["rm", "-f"]]
        assert chmods, f"expected chmod 600 of the header file, got {cmds}"
        assert rms, f"expected the header file to be removed, got {cmds}"
        # chmod target and rm target are the same staged header path.
        assert chmods[0][2] == rms[0][2]


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

        def mock_docker_exec(container_id, cmd, timeout=120, workdir="/workspace", user=None):
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
        # The project .mcp.json goes to /workspace/.mcp.json; the user-level dir
        # gets the same .mcp.json PLUS the enabledMcpjsonServers trust
        # settings.json written by _trust_project_mcp_servers
        # (EnterpriseBench-rryas.4) — both are real writes.
        assert any(
            d.endswith("/home/agent/.claude/.mcp.json") for d in user_writes
        ), f"Expected user-level .mcp.json, got {user_writes}"
        assert any(
            d.endswith("/home/agent/.claude/settings.json") for d in user_writes
        ), f"Expected user-level trust settings.json, got {user_writes}"


class TestMcpConfigOwnership:
    """The agent must be able to read the config written for it.

    ``docker cp`` stamps the copied file with the host temp file's uid (1000)
    and 0600, and the image's default exec user is ``agent`` -- so an
    unqualified ``chown agent:agent`` is a no-op run by a user who cannot
    perform it. Whether the agent could read its own MCP config then came down
    to whether uid 1000 happened to be ``agent`` in that task's base image
    (Python: yes; Node: it is ``node``). Claude Code reports the unreadable
    file as a config *parse* failure, so the symptom names the wrong cause.
    """

    @staticmethod
    def _configure(exec_side_effect):
        def mock_mcp_exec(container_id, cmd, timeout=30, env_names=()):
            return MagicMock(returncode=0, stdout="sourcegraph  Connected", stderr="")

        with (
            patch("run_task._docker_exec", side_effect=exec_side_effect),
            patch("run_task._docker_cp", MagicMock()),
            patch("run_task._verify_mcp_endpoint", return_value=True),
            patch("run_task._mcp_exec", side_effect=mock_mcp_exec),
            patch.dict(os.environ, {"SOURCEGRAPH_ACCESS_TOKEN": "sgp_test"}),
        ):
            return _configure_mcp("test-container", "mcp_only")

    def test_every_config_path_is_chowned_to_agent_as_root(self) -> None:
        calls: list[tuple[list[str], str | None]] = []

        def capture(container_id, cmd, timeout=120, workdir="/workspace", user=None):
            calls.append((cmd, user))
            return MagicMock(returncode=0, stdout="", stderr="")

        assert self._configure(capture) is True

        for path in (
            "/workspace/.mcp.json",
            "/home/agent/.claude/.mcp.json",
            "/home/agent/.mcp.json",
        ):
            chowns = [
                user
                for cmd, user in calls
                if cmd[:2] == ["chown", "agent:agent"] and cmd[-1] == path
            ]
            assert chowns == ["root"], f"{path} was not chowned as root: {chowns}"

    def test_a_failed_chown_fails_preflight(self) -> None:
        def capture(container_id, cmd, timeout=120, workdir="/workspace", user=None):
            if cmd[:2] == ["chown", "agent:agent"] and cmd[-1].endswith(".mcp.json"):
                return MagicMock(returncode=1, stdout="", stderr="Operation not permitted")
            return MagicMock(returncode=0, stdout="", stderr="")

        assert self._configure(capture) is False

    def test_a_config_the_agent_cannot_read_fails_preflight(self) -> None:
        """The readability probe runs as the agent, not as root."""

        def capture(container_id, cmd, timeout=120, workdir="/workspace", user=None):
            if cmd[:2] == ["test", "-r"]:
                assert user == "agent", f"readability probed as {user!r}, not the agent"
                return MagicMock(returncode=1, stdout="", stderr="")
            return MagicMock(returncode=0, stdout="", stderr="")

        assert self._configure(capture) is False


# ---------------------------------------------------------------------------
# Container permission helpers (bead EnterpriseBench-0l2a / s58f)
# ---------------------------------------------------------------------------


class TestChownToAgent:
    def test_chowns_as_root_with_quoted_paths(self) -> None:
        ok = MagicMock(returncode=0, stdout="", stderr="")

        # Both paths are ones the agent legitimately owns. /workspace/.task used
        # to be in this list, which is precisely the hole the grading-asset seal
        # closes — _chown_to_agent now refuses it (bead EnterpriseBench-8krz5).
        with patch("run_task.subprocess.run", return_value=ok) as mock_run:
            _chown_to_agent("cid", ["/workspace/instruction.md", "/workspace/.mcp.json"])

        cmd = mock_run.call_args.args[0]
        assert cmd[:7] == ["docker", "exec", "-w", "/workspace", "-u", "root", "cid"]
        script = cmd[-1]
        assert "chown -R agent:agent" in script
        assert "/workspace/instruction.md" in script
        assert "/workspace/.mcp.json" in script

    def test_skips_missing_paths_via_existence_check(self) -> None:
        ok = MagicMock(returncode=0, stdout="", stderr="")

        with patch("run_task.subprocess.run", return_value=ok) as mock_run:
            _chown_to_agent("cid", ["/workspace/.mcp.json"])

        script = mock_run.call_args.args[0][-1]
        assert '[ -e "$f" ]' in script

    def test_logs_error_on_chown_failure(
        self, caplog: pytest.LogCaptureFixture
    ) -> None:
        fail = MagicMock(returncode=1, stdout="", stderr="chown: not permitted")

        with patch("run_task.subprocess.run", return_value=fail):
            with caplog.at_level("ERROR", logger="run_task"):
                _chown_to_agent("cid", ["/workspace/instruction.md"])

        assert any("chown to agent FAILED" in r.message for r in caplog.records)


class TestAssertAgentReadable:
    def test_all_readable_returns_true(self) -> None:
        ok = MagicMock(returncode=0, stdout="", stderr="")

        with patch("run_task.subprocess.run", return_value=ok) as mock_run:
            readable, err = _assert_agent_readable(
                "cid", ["/workspace/instruction.md", "/workspace/.mcp.json"]
            )

        assert readable is True
        assert err == ""
        assert mock_run.call_count == 2
        cmd = mock_run.call_args_list[0].args[0]
        assert cmd[:7] == ["docker", "exec", "-w", "/workspace", "-u", "agent", "cid"]
        assert cmd[7:] == ["test", "-r", "/workspace/instruction.md"]

    def test_unreadable_path_returns_false_with_path_in_error(self) -> None:
        fail = MagicMock(returncode=1, stdout="", stderr="")

        with patch("run_task.subprocess.run", return_value=fail):
            readable, err = _assert_agent_readable("cid", ["/workspace/.mcp.json"])

        assert readable is False
        assert "/workspace/.mcp.json" in err


class TestScanMcpConfigError:
    def test_missing_log_is_not_an_error(self, tmp_path: Path) -> None:
        assert _scan_mcp_config_error(tmp_path) is False

    def test_clean_log_is_not_an_error(self, tmp_path: Path) -> None:
        (tmp_path / "agent_stderr.log").write_text("all good\n")
        assert _scan_mcp_config_error(tmp_path) is False

    @pytest.mark.parametrize(
        "content",
        [
            "Invalid MCP configuration in /home/agent/.mcp.json",
            "bash: /workspace/instruction.md: Permission denied",
            "Error: EACCES reading /workspace/.mcp.json",
        ],
    )
    def test_detects_noop_markers(self, tmp_path: Path, content: str) -> None:
        (tmp_path / "agent_stderr.log").write_text(content)
        assert _scan_mcp_config_error(tmp_path) is True
