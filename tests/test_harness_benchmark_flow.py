"""Stubbed CLI-to-runner flows for generated benchmark harnesses."""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import patch

import pytest

from scripts import run_benchmark


def _write_task(path: Path, task_id: str = "harness-flow-001") -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        f"""
[task]
id = "{task_id}"
suite = "customer_escalation"
difficulty = "medium"
session_type = "single"
task_type = "error_provenance"
description = "Harness flow fixture"
prompt = "Inspect the repository."

[[repos]]
url = "github.com/example/repo"
rev = "v1.0.0"
path = "repo"
role = "primary"
"""
    )
    return path


@pytest.mark.parametrize(
    ("harness", "model", "label"),
    [
        ("codex", "gpt-5.6-sol", "codex-gpt-5-6-sol-2507d95681"),
        (
            "opencode",
            "openrouter/deepseek/deepseek-v4-pro",
            "opencode-openrouter-deepseek-deepseek-v4-pro-600d7d9311",
        ),
    ],
)
def test_cli_threads_generated_harness_to_single_task_runner(
    tmp_path: Path,
    harness: str,
    model: str,
    label: str,
) -> None:
    task_path = _write_task(tmp_path / "task.toml")
    captured: dict[str, object] = {}

    def fake_run_task(task, *, passthrough_args, dry_run=False, mode="baseline"):
        captured.update(
            task=task,
            passthrough_args=passthrough_args,
            dry_run=dry_run,
            mode=mode,
        )
        return run_benchmark.TaskResult(
            task_id=task.task_id,
            difficulty=task.difficulty,
            status="dry-run",
            mode=mode,
        )

    with patch.object(run_benchmark, "run_task", side_effect=fake_run_task):
        exit_code = run_benchmark.main(
            [
                str(task_path),
                "--harness",
                harness,
                "--model",
                model,
                "--dry-run",
            ]
        )

    assert exit_code == 0
    forwarded = captured["passthrough_args"]
    assert isinstance(forwarded, list)
    assert forwarded[forwarded.index("--harness") + 1] == harness
    assert forwarded[forwarded.index("--model") + 1] == model
    assert forwarded[forwarded.index("--variant-label") + 1] == label
    assert captured["dry_run"] is True


def test_generated_harness_refuses_chain_tasks_before_dispatch(
    tmp_path: Path,
) -> None:
    task_path = _write_task(tmp_path / "task.toml")
    text = task_path.read_text().replace(
        'session_type = "single"', 'session_type = "chain"'
    )
    task_path.write_text(text)

    with pytest.raises(SystemExit) as exc, patch.object(
        run_benchmark,
        "run_task",
        side_effect=AssertionError("unsupported task must not dispatch"),
    ):
        run_benchmark.main(
            [
                str(task_path),
                "--harness",
                "codex",
                "--model",
                "gpt-5.6-sol",
            ]
        )

    assert exc.value.code == 2


def test_labeled_completion_does_not_reuse_legacy_baseline_result(
    tmp_path: Path,
) -> None:
    legacy = tmp_path / "task-a" / "results.json"
    legacy.parent.mkdir(parents=True)
    legacy.write_text(json.dumps({"success": True}))

    assert not run_benchmark.is_task_completed(
        "task-a",
        results_dir=tmp_path,
        mode="baseline",
        variant_label="codex-gpt-5-6-sol",
    )


def test_labeled_cost_does_not_reuse_legacy_baseline_cost(tmp_path: Path) -> None:
    legacy = tmp_path / "task-a" / "results.json"
    legacy.parent.mkdir(parents=True)
    legacy.write_text(json.dumps({"tool_usage": {"cost_usd": 12.5}}))

    assert (
        run_benchmark.extract_task_cost(
            "task-a",
            results_dir=tmp_path,
            mode="baseline",
            variant_label="codex-gpt-5-6-sol",
        )
        == 0.0
    )
