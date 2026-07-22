"""Harness-side staging must be identity-explicit, never uid-accidental.

In images whose base already owns uid 1000 (node:20-bookworm creates `node`),
`useradd agent` lands on uid 1001. A docker-cp'd file arrives with the HOST
uid (1000), so a chown/chmod/read executed as the image's default user
(`agent`) silently fails or reads nothing — the MCP pre-flight curl sent no
auth header and 401'd, and the sgx wrapper could not be written to
/usr/local/bin (observed on dead-code-001, the only node-based pilot task).
Go/Python/Java images only worked because `agent` happened to get uid 1000.

The contract pinned here: every _docker_exec that stages, chowns, or probes
harness-side assets in _verify_mcp_endpoint, the MCP-config staging, and the
sgx install runs explicitly as root. Agent-facing execs (the sgx PATH probe)
stay on the default identity on purpose — they validate the agent's view.
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT / "scripts" / "orchestration"))

spec = importlib.util.spec_from_file_location(
    "run_task_staging", REPO_ROOT / "scripts" / "orchestration" / "run_task.py"
)
run_task = importlib.util.module_from_spec(spec)
spec.loader.exec_module(run_task)


def _completed(code: int = 0, stdout: str = "", stderr: str = ""):
    return SimpleNamespace(returncode=code, stdout=stdout, stderr=stderr)


class TestVerifyMcpEndpointRunsAsRoot:
    def test_chmod_and_curl_pass_root(self, tmp_path: Path) -> None:
        calls = []

        def fake_exec(container_id, cmd, timeout=120, workdir="/workspace", user=None):
            calls.append((cmd, user))
            if cmd[0] == "curl":
                return _completed(0, stdout="405")
            return _completed(0)

        with patch.object(run_task, "_docker_exec", side_effect=fake_exec), patch.object(
            run_task, "_docker_cp"
        ):
            ok = run_task._verify_mcp_endpoint("cid", "sgp_test_token")

        assert ok
        staging = [(c, u) for c, u in calls if c[0] in ("chmod", "curl", "rm")]
        assert staging, "expected chmod/curl staging calls"
        for cmd, user in staging:
            if cmd[0] in ("chmod", "curl"):
                assert user == "root", (
                    f"{cmd[0]} ran as {user!r}; harness staging must be "
                    f"identity-explicit root — uid-accident images (agent=1001) "
                    f"break otherwise"
                )


class TestSgxInstallStagesAsRoot:
    def test_wrapper_write_and_interpreter_ladder_are_root(self) -> None:
        calls = []

        def fake_exec(container_id, cmd, timeout=120, workdir="/workspace", user=None):
            calls.append((cmd, user))
            joined = " ".join(cmd)
            if "sgx --help" in joined:
                return _completed(0, stdout="SG_CLI_OK\n")
            if cmd[0] == "sh" and "sg_version" in joined:
                return _completed(0, stdout="ok")
            return _completed(0)

        with patch.object(run_task, "_docker_exec", side_effect=fake_exec), patch.object(
            run_task, "_docker_cp"
        ), patch.object(run_task, "SGX_CLI_SRC", Path(__file__)):
            run_task._install_sgx("cid", "cli")

        wrapper_writes = [
            (c, u)
            for c, u in calls
            if c[0] == "sh" and any("/usr/local/bin/sgx" in part for part in c)
        ]
        interpreter = [
            (c, u)
            for c, u in calls
            if c[0] == "sh" and any("apt-get" in part for part in c)
        ]
        assert wrapper_writes, "expected the sgx wrapper write"
        for cmd, user in wrapper_writes:
            joined = " ".join(cmd)
            if "base64 -d > /usr/local/bin/sgx" in joined:
                assert user == "root", "sgx wrapper write must run as root"
        for cmd, user in interpreter:
            assert user == "root", "interpreter install ladder must run as root"

        probes = [
            (c, u)
            for c, u in calls
            if c[0] == "sh" and any("sgx --help" in part for part in c)
        ]
        assert probes, "expected the PATH probe"
        for cmd, user in probes:
            assert user is None, (
                "the sgx PATH probe validates the AGENT's view and must stay "
                "on the default identity"
            )
