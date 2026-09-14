"""Repository-health failures must stop before any paid agent execution."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

from scripts.orchestration import run_task


def test_failed_repository_health_is_invalid_and_never_runs_agent(
    tmp_path: Path,
) -> None:
    task_toml = tmp_path / "task.toml"
    task_toml.write_text(
        '[task]\nid = "health-preflight-001"\nsuite = "test"\n'
        'task_type = "error_provenance"\nsession_type = "single"\n'
        '\n[artifacts]\nrequired = ["answer"]\n'
    )
    task_data = {
        "task": {
            "id": "health-preflight-001",
            "suite": "test",
            "task_type": "error_provenance",
            "session_type": "single",
        },
        "repos": [{"path": "missing-repo"}],
        "checkpoints": [],
    }
    config = run_task.TaskRunConfig(
        task_toml=task_toml,
        harness="claude",
        model="claude-sonnet-5",
        agent_command="claude -p",
        no_build=True,
        output_dir=tmp_path / "out",
    )

    with (
        patch.object(run_task, "_parse_task", return_value=task_data),
        patch.object(run_task, "_check_disk_space", return_value=True),
        patch.object(run_task, "_docker_create_container", return_value="cid"),
        patch.object(run_task, "_docker_start"),
        patch.object(run_task, "_setup_container"),
        patch.object(run_task, "_run_health_check", return_value=False),
        patch.object(run_task, "_install_claude_cli", return_value=True),
        patch.object(run_task, "_chown_to_agent"),
        patch.object(run_task, "_assert_agent_readable", return_value=(True, "")),
        patch.object(run_task, "_run_agent", return_value=(0, 0.1)) as agent,
        patch.object(run_task, "_extract_tool_usage", return_value={}),
        patch.object(run_task, "_run_scoring", return_value={"task_score": 1.0}),
        patch.object(run_task, "_save_results"),
        patch.object(run_task, "_docker_stop_rm"),
    ):
        result = run_task.run_task(config)

    agent.assert_not_called()
    assert result.phase == "repository_health_failed"
    assert result.failure_class == "infra_repository_health"
    assert result.status == run_task.RUN_STATUS_INVALID
    assert result.success is False
    assert result.scores == {}
