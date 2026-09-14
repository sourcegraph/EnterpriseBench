"""Integration-boundary tests for installing and running agent harness CLIs."""

from __future__ import annotations

import json
import subprocess
import tomllib
from pathlib import Path
from unittest.mock import patch

import pytest

from agents.harnesses.registry import build_harness_plan
from scripts.orchestration import run_task


def _completed(
    returncode: int = 0, stdout: str = "", stderr: str = ""
) -> subprocess.CompletedProcess[str]:
    return subprocess.CompletedProcess(
        args=["docker"], returncode=returncode, stdout=stdout, stderr=stderr
    )


def test_install_harness_cli_uses_pinned_package_and_verifies_binary() -> None:
    plan = build_harness_plan("codex", model="gpt-5.6-sol", mode="baseline")
    calls: list[list[str]] = []

    def fake_exec(_container_id: str, argv: list[str], **_kwargs):
        calls.append(argv)
        if len(calls) == 1:
            return _completed(1, stderr="not found")
        return _completed(0, stdout="codex-cli 0.145.0")

    with patch.object(run_task, "_docker_exec", side_effect=fake_exec):
        assert run_task._install_harness_cli("container-1", plan) is True

    assert calls == [
        ["codex", "--version"],
        ["npm", "install", "-g", plan.npm_package],
        ["codex", "--version"],
    ]


def test_install_harness_cli_fails_when_binary_still_missing() -> None:
    plan = build_harness_plan(
        "opencode",
        model="openrouter/openai/gpt-oss-120b",
        mode="baseline",
    )
    with patch.object(run_task, "_docker_exec", return_value=_completed(1)):
        assert run_task._install_harness_cli("container-1", plan) is False


def test_install_harness_cli_replaces_an_unpinned_version() -> None:
    plan = build_harness_plan("codex", model="gpt-5.6-sol", mode="baseline")
    calls: list[list[str]] = []

    def fake_exec(_container_id: str, argv: list[str], **_kwargs):
        calls.append(argv)
        if len(calls) == 1:
            return _completed(stdout="codex-cli 0.144.0")
        return _completed(stdout="codex-cli 0.145.0")

    with patch.object(run_task, "_docker_exec", side_effect=fake_exec):
        assert run_task._install_harness_cli("container-1", plan) is True

    assert calls == [
        ["codex", "--version"],
        ["npm", "install", "-g", plan.npm_package],
        ["codex", "--version"],
    ]


def test_install_harness_cli_installs_as_root_but_verifies_as_the_agent() -> None:
    """The global install needs root; the binary must still work unprivileged.

    `npm install -g` writes to /usr/local/lib/node_modules, root-owned on the
    Node base images and agent-owned on the Python ones. Installing as the
    default user made harness availability depend on which language a task's
    repos happened to need: every Codex run of the one JS-repo task in the
    retrieval slice died `infra_harness_install` while the other nine passed.
    The version check stays unprivileged on purpose -- it is the only step that
    proves `agent` can execute what root installed.
    """
    plan = build_harness_plan("codex", model="gpt-5.6-sol", mode="baseline")
    calls: list[tuple[list[str], str | None]] = []

    def fake_exec(_container_id: str, argv: list[str], **kwargs):
        calls.append((argv, kwargs.get("user")))
        if len(calls) == 1:
            return _completed(1, stderr="not found")
        return _completed(0, stdout="codex-cli 0.145.0")

    with patch.object(run_task, "_docker_exec", side_effect=fake_exec):
        assert run_task._install_harness_cli("container-1", plan) is True

    assert calls == [
        (["codex", "--version"], None),
        (["npm", "install", "-g", plan.npm_package], "root"),
        (["codex", "--version"], None),
    ]


def test_prepare_codex_credentials_copies_chatgpt_auth_with_private_mode(
    tmp_path: Path,
) -> None:
    codex_home = tmp_path / "codex-home"
    codex_home.mkdir()
    auth_path = codex_home / "auth.json"
    auth_path.write_text('{"auth_mode":"chatgpt"}')
    plan = build_harness_plan("codex", model="gpt-5.6-sol", mode="baseline")
    exec_calls: list[list[str]] = []
    exec_users: list[str | None] = []
    copies: list[tuple[str, str]] = []

    with (
        patch.object(
            run_task,
            "_docker_exec",
            side_effect=lambda _cid, argv, **kwargs: (
                exec_calls.append(argv)
                or exec_users.append(kwargs.get("user"))
                or _completed()
            ),
        ),
        patch.object(
            run_task,
            "_docker_cp",
            side_effect=lambda source, target: copies.append((source, target)),
        ),
    ):
        env = run_task._prepare_harness_credentials(
            "container-1",
            plan,
            environ={"CODEX_HOME": str(codex_home)},
        )

    assert env == {"CODEX_HOME": "/home/agent/.codex"}
    assert copies == [(str(auth_path), "container-1:/home/agent/.codex/auth.json")]
    assert ["chmod", "600", "/home/agent/.codex/auth.json"] in exec_calls
    assert ["chown", "-R", "agent:agent", "/home/agent/.codex"] in exec_calls
    # Every step of the credential staging must run as root. `docker cp`
    # stamps the file with the host temp file's uid (1000); `agent` is uid
    # 1000 in the Python base images and 1001 in the Node ones, so an
    # unprivileged chmod is a permitted self-chmod on one and EPERM on the
    # other. Staged as the default user, Codex credentials could be installed
    # only for tasks whose repos happened to be Python: every Codex run of the
    # one JS-repo task in the retrieval slice died in setup_failed.
    assert exec_users == ["root"] * len(exec_calls), exec_users


def test_configure_codex_mcp_stages_its_config_as_root() -> None:
    """Config staging must not depend on the base image's uid layout.

    Third site in the same family as the harness install and the credential
    staging: `docker cp` stamps the config with the host temp file's uid
    (1000), `agent` is 1000 on the Python images and 1001 on the Node ones, so
    an unprivileged chown fails on exactly the JS-repo tasks. The caller
    reports this function's False as "endpoint unreachable or token
    rejected/expired", so the failure reads as a network or credential problem
    while the MCP endpoint is demonstrably healthy for every other task.
    """
    calls: list[tuple[list[str], str | None]] = []

    def fake_exec(_container_id: str, argv: list[str], **kwargs):
        calls.append((argv, kwargs.get("user")))
        return _completed()

    with (
        patch.object(run_task, "_docker_exec", side_effect=fake_exec),
        patch.object(run_task, "_docker_cp", return_value=None),
        patch.object(run_task, "_verify_mcp_endpoint", return_value=True),
        patch.object(
            run_task,
            "_mcp_exec",
            return_value=_completed(stdout="sourcegraph"),
        ),
        patch.dict("os.environ", {"SOURCEGRAPH_ACCESS_TOKEN": "sgp_test"}),
    ):
        assert run_task._configure_codex_mcp("container-1", "mcp_only") is True

    staged = {tuple(argv): user for argv, user in calls}
    assert staged[("mkdir", "-p", "/home/agent/.codex")] == "root"
    assert staged[("chown", "agent:agent", "/home/agent/.codex/config.toml")] == "root"
    assert staged[("chmod", "600", "/home/agent/.codex/config.toml")] == "root"


def test_prepare_codex_credentials_requires_an_explicit_cli_login(
    tmp_path: Path,
) -> None:
    plan = build_harness_plan("codex", model="gpt-5.6-sol", mode="baseline")
    with pytest.raises(FileNotFoundError, match="codex login"):
        run_task._prepare_harness_credentials(
            "container-1",
            plan,
            environ={
                "HOME": str(tmp_path),
                "OPENAI_API_KEY": "not-an-implicit-login",
            },
        )


def test_cleanup_codex_credentials_removes_staged_login_file() -> None:
    plan = build_harness_plan("codex", model="gpt-5.6-sol", mode="baseline")
    with patch.object(
        run_task,
        "_docker_exec",
        return_value=_completed(),
    ) as docker_exec:
        run_task._cleanup_harness_credentials(
            "container-1",
            plan,
            {"CODEX_HOME": "/home/agent/.codex"},
        )

    docker_exec.assert_called_once_with(
        "container-1",
        ["rm", "-f", "/home/agent/.codex/auth.json"],
    )


def test_cleanup_codex_without_staged_login_does_not_touch_container_files() -> None:
    plan = build_harness_plan("codex", model="gpt-5.6-sol", mode="baseline")
    with patch.object(run_task, "_docker_exec") as docker_exec:
        run_task._cleanup_harness_credentials("container-1", plan, {})

    docker_exec.assert_not_called()


def test_container_cleanup_forces_remove_after_stop_timeout() -> None:
    removed = _completed()
    with patch.object(
        run_task.subprocess,
        "run",
        side_effect=[
            subprocess.TimeoutExpired(["docker", "stop"], 30),
            removed,
        ],
    ) as subprocess_run:
        run_task._docker_stop_rm("container-1")

    assert subprocess_run.call_args_list[0].args[0] == [
        "docker",
        "stop",
        "-t",
        "5",
        "container-1",
    ]
    assert subprocess_run.call_args_list[1].args[0] == [
        "docker",
        "rm",
        "-f",
        "container-1",
    ]


def test_configure_codex_mcp_uses_env_reference_without_persisting_token() -> None:
    copied: list[tuple[str, str]] = []

    def capture_copy(source: str, destination: str) -> None:
        copied.append((destination, Path(source).read_text()))

    listed = _completed(stdout="sourcegraph  enabled")
    with (
        patch.dict(
            run_task.os.environ,
            {"SOURCEGRAPH_ACCESS_TOKEN": "sourcegraph-secret"},
        ),
        patch.object(run_task, "_verify_mcp_endpoint", return_value=True),
        patch.object(run_task, "_docker_cp", side_effect=capture_copy),
        patch.object(run_task, "_docker_exec", return_value=_completed()),
        patch.object(run_task, "_mcp_exec", return_value=listed) as mcp_exec,
    ):
        configured = run_task._configure_mcp("container-1", "mcp_only", harness="codex")

    assert configured is True
    destination, raw_config = next(
        item for item in copied if item[0].endswith("/home/agent/.codex/config.toml")
    )
    assert destination == "container-1:/home/agent/.codex/config.toml"
    assert "sourcegraph-secret" not in raw_config
    config = tomllib.loads(raw_config)
    sourcegraph = config["mcp_servers"]["sourcegraph"]
    assert sourcegraph["url"] == run_task.SOURCEGRAPH_MCP_ENDPOINT
    assert sourcegraph["bearer_token_env_var"] == "SOURCEGRAPH_ACCESS_TOKEN"
    assert sourcegraph["required"] is True
    mcp_exec.assert_called_once_with(
        "container-1",
        ["codex", "mcp", "list"],
        env_names=("SOURCEGRAPH_ACCESS_TOKEN",),
    )


def test_configure_opencode_mcp_uses_env_reference_without_persisting_token() -> None:
    copied: list[tuple[str, str]] = []

    def capture_copy(source: str, destination: str) -> None:
        copied.append((destination, Path(source).read_text()))

    listed = _completed(stdout="sourcegraph connected")
    with (
        patch.dict(
            run_task.os.environ,
            {"SOURCEGRAPH_ACCESS_TOKEN": "sourcegraph-secret"},
        ),
        patch.object(run_task, "_verify_mcp_endpoint", return_value=True),
        patch.object(run_task, "_docker_cp", side_effect=capture_copy),
        patch.object(run_task, "_docker_exec", return_value=_completed()),
        patch.object(run_task, "_mcp_exec", return_value=listed) as mcp_exec,
    ):
        configured = run_task._configure_mcp(
            "container-1", "mcp_only", harness="opencode"
        )

    assert configured is True
    destination, raw_config = next(
        item
        for item in copied
        if item[0].endswith("/home/agent/.config/opencode/opencode.jsonc")
    )
    assert destination == "container-1:/home/agent/.config/opencode/opencode.jsonc"
    assert "sourcegraph-secret" not in raw_config
    config = json.loads(raw_config)
    sourcegraph = config["mcp"]["sourcegraph"]
    assert sourcegraph == {
        "type": "remote",
        "url": run_task.SOURCEGRAPH_MCP_ENDPOINT,
        "enabled": True,
        "oauth": False,
        "headers": {
            "Authorization": "token {env:SOURCEGRAPH_ACCESS_TOKEN}",
        },
    }
    mcp_exec.assert_called_once_with(
        "container-1",
        ["opencode", "mcp", "list"],
        env_names=("SOURCEGRAPH_ACCESS_TOKEN",),
    )


def test_configure_codex_finder_mode_points_at_telemetry_proxy() -> None:
    copied: list[str] = []

    def capture_copy(source: str, _destination: str) -> None:
        copied.append(Path(source).read_text())

    with (
        patch.dict(
            run_task.os.environ,
            {"SOURCEGRAPH_ACCESS_TOKEN": "sourcegraph-secret"},
        ),
        patch.object(run_task, "_verify_mcp_endpoint", return_value=True),
        patch.object(run_task, "_start_mcp_telemetry_proxy", return_value=True),
        patch.object(run_task, "_docker_cp", side_effect=capture_copy),
        patch.object(run_task, "_docker_exec", return_value=_completed()),
        patch.object(
            run_task,
            "_mcp_exec",
            return_value=_completed(stdout="sourcegraph enabled"),
        ),
    ):
        configured = run_task._configure_mcp(
            "container-1", "mcp_code_finder", harness="codex"
        )

    assert configured is True
    raw_config = next(text for text in copied if "[mcp_servers.sourcegraph]" in text)
    assert run_task.MCP_PROXY_ENDPOINT in raw_config
    assert run_task.SOURCEGRAPH_MCP_ENDPOINT not in raw_config


def test_capture_finder_telemetry_copies_root_trace_and_merges_outer_usage(
    tmp_path: Path,
) -> None:
    result = run_task.TaskRunResult(
        task_id="cal-err-flask-blueprint-001",
        tool_usage={
            "total_input_tokens": 10,
            "total_output_tokens": 5,
            "cost_usd": 0.1,
        },
    )
    records = [
        {
            "direction": "request",
            "method": "tools/call",
            "id": 1,
            "tool": "code_finder",
            "arguments": {"query": "find it"},
        },
        {
            "direction": "response",
            "id": 1,
            "tool": "code_finder",
            "status": 200,
            "is_error": False,
            "meta": {
                "sourcegraphToolTelemetry": {
                    "subAgentTurns": 2,
                    "subAgentTotalTokens": 30,
                }
            },
        },
    ]

    def copy_trace(_source: str, destination: str) -> None:
        Path(destination).write_text(
            "\n".join(json.dumps(record) for record in records) + "\n"
        )

    with patch.object(run_task, "_docker_cp", side_effect=copy_trace) as docker_cp:
        run_task._capture_mcp_telemetry(
            result,
            "container-1",
            tmp_path,
            mode="mcp_code_finder",
            expected_repo_count=1,
        )

    docker_cp.assert_called_once_with(
        f"container-1:{run_task.MCP_PROXY_TRACE_PATH}",
        str(tmp_path / "mcp_trace.jsonl"),
    )
    assert result.tool_usage["retrieval"]["valid"] is True
    assert result.tool_usage["retrieval"]["combined"]["total_tokens"] == 45


def test_mcp_exec_forwards_secret_by_name_not_value() -> None:
    completed = _completed(stdout="sourcegraph enabled")
    with (
        patch.dict(
            run_task.os.environ,
            {"SOURCEGRAPH_ACCESS_TOKEN": "sourcegraph-secret"},
        ),
        patch.object(
            run_task.subprocess, "run", return_value=completed
        ) as subprocess_run,
    ):
        result = run_task._mcp_exec(
            "container-1",
            ["codex", "mcp", "list"],
            env_names=("SOURCEGRAPH_ACCESS_TOKEN",),
        )

    assert result is completed
    command = subprocess_run.call_args.args[0]
    assert "SOURCEGRAPH_ACCESS_TOKEN" in command
    assert "sourcegraph-secret" not in command


def test_prepare_opencode_credentials_requires_openrouter_key() -> None:
    plan = build_harness_plan(
        "opencode",
        model="openrouter/openai/gpt-oss-120b",
        mode="baseline",
    )
    with pytest.raises(FileNotFoundError, match="OPENROUTER_API_KEY"):
        run_task._prepare_harness_credentials("container-1", plan, environ={})


def test_run_agent_passes_generated_argv_without_shell_interpolation(
    tmp_path: Path,
) -> None:
    plan = build_harness_plan(
        "opencode",
        model="openrouter/deepseek/deepseek-v4-pro",
        mode="baseline",
    )
    completed = _completed(stdout='{"type":"step_finish"}\n')

    with patch.object(run_task.subprocess, "run", return_value=completed) as run:
        exit_code, _duration = run_task._run_agent(
            "container-1",
            plan.command,
            120,
            tmp_path,
            env_extra={"OPENROUTER_API_KEY": "secret"},
        )

    assert exit_code == 0
    command = run.call_args.args[0]
    assert command[-len(plan.command) :] == list(plan.command)
    assert command[-len(plan.command) - 1] == "enterprisebench-agent"
    assert 'exec "$@" < /workspace/instruction.md' in command[-len(plan.command) - 2]
    assert (tmp_path / "agent" / "stdout.log").read_text() == completed.stdout


def test_run_agent_rejects_newlines_in_secret_env_values(tmp_path: Path) -> None:
    plan = build_harness_plan("codex", model="gpt-5.6-sol", mode="baseline")
    with (
        patch.object(run_task.subprocess, "run") as run,
        pytest.raises(ValueError, match="unsafe newline"),
    ):
        run_task._run_agent(
            "container-1",
            plan.command,
            120,
            tmp_path,
            env_extra={"OPENAI_API_KEY": "secret\nINJECTED=value"},
        )

    run.assert_not_called()
