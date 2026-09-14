"""Root-cause console trace ingestion and self-contained HTML generation."""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

from scripts.analysis.rootcause_console import (
    build_run_cell,
    merge_console,
    normalize_trace,
    redact,
)


def _write_json(path: Path, value: object) -> None:
    path.write_text(json.dumps(value))


def _make_task(tmp_path: Path) -> Path:
    task_dir = tmp_path / "task"
    task_dir.mkdir()
    (task_dir / "instruction.md").write_text("Investigate the failure.")
    _write_json(task_dir / "ground_truth.json", {"required": ["source.py"]})
    _write_json(task_dir / "expected_solution.json", {"answer": "source.py"})
    return task_dir


def _make_run(
    tmp_path: Path,
    *,
    harness: str,
    model: str,
    variant_label: str,
    records: list[dict],
    mode: str = "baseline",
) -> Path:
    run_dir = tmp_path / f"{mode}--{variant_label}"
    run_dir.mkdir()
    result = {
        "task_id": "task-001",
        "phase": "complete",
        "success": True,
        "status": "",
        "failure_class": None,
        "image_tag": "eb-task-001",
        "config": {
            "source": "mirror",
            "harness": harness,
            "model": model,
            "timeout": 1800,
            "mode": mode,
            "variant_label": variant_label,
        },
        "task_metadata": {
            "suite": "customer_escalation",
            "task_type": "error_provenance",
            "difficulty": "medium",
        },
        "timing": {"agent": 12.5, "scoring": 0.5},
        "tool_usage": {
            "total_input_tokens": 100,
            "total_output_tokens": 20,
            "cost_usd": 0.03,
            "num_turns": 1,
            "mcp_tool_calls": 0,
            "sgx_tool_calls": 0,
        },
        "scores": {
            "task_score": 0.75,
            "checkpoints": [
                {
                    "name": "source",
                    "weight": 1.0,
                    "score": 0.75,
                    "verifier_ran": True,
                    "detail": "matched",
                }
            ],
        },
    }
    _write_json(run_dir / "results.json", result)
    (run_dir / "agent_stdout.log").write_text(
        "\n".join(json.dumps(record) for record in records) + "\n"
    )
    return run_dir


def test_normalizes_codex_trace_and_labels_native_activity() -> None:
    records = [
        {"type": "turn.started"},
        {
            "type": "item.completed",
            "item": {
                "type": "command_execution",
                "command": "rg register",
                "aggregated_output": "src/app.py",
                "status": "completed",
                "exit_code": 0,
            },
        },
        {
            "type": "item.completed",
            "item": {"type": "agent_message", "text": "Found it."},
        },
        {
            "type": "item.completed",
            "item": {
                "type": "file_change",
                "status": "completed",
                "changes": [{"path": "/workspace/answer.json", "kind": "add"}],
            },
        },
        {
            "type": "item.completed",
            "item": {
                "type": "mcp_tool_call",
                "server": "sourcegraph",
                "tool": "keyword_search",
                "status": "completed",
                "arguments": {"query": "register_blueprint"},
                "result": {"content": [{"type": "text", "text": "match"}]},
            },
        },
        {"type": "turn.completed", "usage": {"input_tokens": 100}},
    ]

    trace, activity, writes = normalize_trace(records)

    assert activity == {
        "provider": "codex",
        "primary_unit": "turn",
        "primary_count": 1,
        "label": "1 Codex turn",
        "work_items": 4,
        "tool_uses": 2,
        "agent_messages": 1,
        "file_changes": 1,
    }
    assert [event["kind"] for event in trace] == [
        "tool",
        "message",
        "file",
        "tool",
        "boundary",
    ]
    assert trace[0]["result"] == "src/app.py"
    assert trace[3]["name"] == "sourcegraph.keyword_search"
    assert writes[0]["path"] == "/workspace/answer.json"


def test_redacts_values_stored_under_sensitive_keys() -> None:
    payload = {
        "api_key": "unprefixed-secret-value",
        "client_secret": "oauth-client-secret",
        "nested": {"Authorization": "opaque-token"},
        "tokens": {
            "refresh_token": "refresh-secret",
            "id_token": "identity-secret",
        },
        "safe": "visible",
    }

    assert redact(payload) == {
        "api_key": "[REDACTED]",
        "client_secret": "[REDACTED]",
        "nested": {"Authorization": "[REDACTED]"},
        "tokens": {
            "refresh_token": "[REDACTED]",
            "id_token": "[REDACTED]",
        },
        "safe": "visible",
    }


def test_redacts_refresh_and_id_tokens_embedded_in_trace_text() -> None:
    raw = (
        '{"refresh_token":"refresh-secret",'
        '"id_token":"identity-secret","safe":"visible"}'
    )

    redacted = redact(raw)

    assert "refresh-secret" not in redacted
    assert "identity-secret" not in redacted
    assert "visible" in redacted


def test_normalizes_opencode_steps_without_calling_them_turns() -> None:
    records = [
        {"type": "step_start", "timestamp": 1, "part": {"type": "step-start"}},
        {
            "type": "tool_use",
            "timestamp": 2,
            "part": {
                "type": "tool",
                "tool": "bash",
                "state": {
                    "status": "completed",
                    "input": {"command": "pwd"},
                    "output": "/workspace",
                },
            },
        },
        {
            "type": "tool_use",
            "timestamp": 2,
            "part": {
                "type": "tool",
                "tool": "write",
                "state": {
                    "status": "completed",
                    "input": {
                        "filePath": "/workspace/agent_output/answer.json",
                        "content": "{}",
                    },
                    "output": "wrote file",
                },
            },
        },
        {
            "type": "tool_use",
            "timestamp": 2,
            "part": {
                "type": "tool",
                "tool": "sourcegraph_keyword_search",
                "state": {
                    "status": "completed",
                    "input": {"query": "register_blueprint"},
                    "output": "match",
                },
            },
        },
        {
            "type": "text",
            "timestamp": 3,
            "part": {"type": "text", "text": "Done."},
        },
        {
            "type": "step_finish",
            "timestamp": 4,
            "part": {
                "type": "step-finish",
                "reason": "stop",
                "tokens": {"input": 10, "output": 2},
                "cost": 0.01,
            },
        },
    ]

    trace, activity, writes = normalize_trace(records)

    assert activity["provider"] == "opencode"
    assert activity["primary_unit"] == "step"
    assert activity["primary_count"] == 1
    assert activity["tool_uses"] == 3
    assert activity["agent_messages"] == 1
    assert activity["file_changes"] == 1
    assert [event["kind"] for event in trace] == [
        "tool",
        "tool",
        "tool",
        "message",
        "boundary",
    ]
    assert trace[2]["name"] == "sourcegraph.keyword_search"
    assert writes[0]["path"] == "/workspace/agent_output/answer.json"


def test_build_run_cell_redacts_secrets_and_preserves_trace(
    tmp_path: Path,
) -> None:
    task_dir = _make_task(tmp_path)
    run_dir = _make_run(
        tmp_path,
        harness="opencode",
        model="openrouter/moonshotai/kimi-k3",
        variant_label="opencode-openrouter-kimi-k3",
        records=[
            {
                "type": "tool_use",
                "part": {
                    "type": "tool",
                    "tool": "bash",
                    "state": {
                        "status": "completed",
                        "input": {
                            "command": (
                                "OPENROUTER_API_KEY=sk-or-v1-secret "
                                "--api-key ultra-secret command"
                            )
                        },
                        "output": "Authorization: Bearer top-secret",
                    },
                },
            },
            {
                "type": "step_finish",
                "part": {
                    "type": "step-finish",
                    "reason": "stop",
                    "tokens": {},
                    "cost": 0.0,
                },
            },
        ],
    )

    cell = build_run_cell(run_dir, task_dir)
    serialized = json.dumps(cell)

    assert cell["run_id"] == "task-001/baseline/opencode-openrouter-kimi-k3"
    assert cell["harness"] == "opencode"
    assert cell["activity"]["label"] == "1 OpenCode step"
    assert "sk-or-v1-secret" not in serialized
    assert "ultra-secret" not in serialized
    assert "top-secret" not in serialized
    assert "[REDACTED]" in serialized
    assert cell["trace"]


def test_codex_zero_cost_is_reported_as_unavailable(tmp_path: Path) -> None:
    task_dir = _make_task(tmp_path)
    run_dir = _make_run(
        tmp_path,
        harness="codex",
        model="gpt-5.6-sol",
        variant_label="codex-gpt-5-6-sol",
        records=[
            {"type": "turn.completed", "usage": {"input_tokens": 100}},
        ],
    )
    result_path = run_dir / "results.json"
    result = json.loads(result_path.read_text())
    result["tool_usage"]["cost_usd"] = 0.0
    _write_json(result_path, result)

    cell = build_run_cell(run_dir, task_dir)

    assert cell["cost"] is None
    assert cell["cost_note"] == "not reported by Codex"


def test_build_run_cell_includes_judge_and_opencode_lifecycle(
    tmp_path: Path,
) -> None:
    task_dir = _make_task(tmp_path)
    run_dir = _make_run(
        tmp_path,
        harness="opencode",
        model="openrouter/moonshotai/kimi-k3",
        variant_label="opencode-kimi-k3-judge-a3",
        records=[],
    )
    result_path = run_dir / "results.json"
    result = json.loads(result_path.read_text())
    result["config"]["judge_model"] = "cc:haiku"
    result["config"]["judge_account"] = 3
    result["tool_usage"]["opencode_lifecycle"] = {
        "observed_duration_ms": 887826,
        "step_starts": 4,
        "step_finishes": 3,
        "last_event_type": "step_start",
        "unfinished_step": True,
        "graded_artifact_path": "/workspace/agent_output/answer.json",
        "graded_artifact_written": True,
        "graded_artifact_write_at_ms": 1785008407349,
        "canonical_answer_written": True,
        "canonical_answer_write_at_ms": 1785008407349,
        "artifact_writes": ["/workspace/agent_output/answer.json"],
    }
    result["scores"]["judge_provenance"] = {
        "backend": "claude_code_cli",
        "provider": "anthropic",
        "model": "haiku",
        "account": 3,
        "executable": "claude-3",
        "cli_version": "2.1.220 (Claude Code)",
    }
    _write_json(result_path, result)

    cell = build_run_cell(run_dir, task_dir)

    assert cell["judge"]["requested"] == {
        "model": "cc:haiku",
        "account": 3,
    }
    assert cell["judge"]["provenance"]["executable"] == "claude-3"
    assert cell["lifecycle"]["unfinished_step"] is True
    assert cell["lifecycle"]["graded_artifact_written"] is True


def test_build_run_cell_preserves_nested_arm_gate_proof(tmp_path: Path) -> None:
    task_dir = _make_task(tmp_path)
    run_dir = _make_run(
        tmp_path,
        harness="claude",
        model="claude-sonnet-5",
        variant_label="claude-sonnet-5",
        records=[
            {
                "type": "result",
                "subtype": "success",
                "is_error": False,
                "num_turns": 1,
                "modelUsage": {},
            }
        ],
    )
    result_path = run_dir / "results.json"
    result = json.loads(result_path.read_text())
    result["provenance"] = {
        "arm_gate_proof": (
            "mode_gate:v1:mcp_only:agent-denied,scorer-readable;repos=2"
        )
    }
    _write_json(result_path, result)

    cell = build_run_cell(run_dir, task_dir)

    assert cell["arm_gate_proof"] == (
        "mode_gate:v1:mcp_only:agent-denied,scorer-readable;repos=2"
    )


def test_merge_console_is_idempotent_and_inlines_ui(tmp_path: Path) -> None:
    console = tmp_path / "rootcause_console.html"
    ui = tmp_path / "ui.js"
    console.write_text(
        '<script id="data" type="application/json">'
        '[{"task":"legacy","mode":"baseline",'
        '"calls":[{"result":"token sgp_1234567890abcdefghijklmnop"}]}]'
        "</script>"
        "<script>oldUi()</script>"
    )
    console.chmod(0o644)
    ui.write_text('document.body.dataset.comparisonContract = "provider-native";')
    cell = {
        "task": "task-001",
        "run_id": "task-001/codex-gpt",
        "mode": "baseline",
        "trace": [{"input": "</script><img src=x onerror=alert(1)>"}],
    }

    merge_console(console, [cell], ui)
    merge_console(console, [cell], ui)

    html = console.read_text()
    payload = html.split('<script id="data" type="application/json">', maxsplit=1)[
        1
    ].split("</script>", maxsplit=1)[0]
    cells = json.loads(payload)
    assert [item["task"] for item in cells] == ["legacy", "task-001"]
    assert "sgp_1234567890abcdefghijklmnop" not in html
    assert html.count("task-001/codex-gpt") == 1
    assert "provider-native" in html
    assert "</script><img" not in html
    assert r"<\/script><img" in html
    assert console.stat().st_mode & 0o777 == 0o644


def test_merge_console_keeps_baseline_and_mcp_runs_for_same_variant(
    tmp_path: Path,
) -> None:
    task_dir = _make_task(tmp_path)
    baseline = build_run_cell(
        _make_run(
            tmp_path,
            harness="codex",
            model="gpt-5.6-sol",
            variant_label="codex-gpt-5-6-sol",
            mode="baseline",
            records=[],
        ),
        task_dir,
    )
    mcp_only = build_run_cell(
        _make_run(
            tmp_path,
            harness="codex",
            model="gpt-5.6-sol",
            variant_label="codex-gpt-5-6-sol",
            mode="mcp_only",
            records=[],
        ),
        task_dir,
    )
    console = tmp_path / "rootcause_console.html"
    ui = tmp_path / "ui.js"
    console.write_text(
        '<script id="data" type="application/json">'
        '[{"task":"task-001","run_id":"task-001/codex-gpt-5-6-sol",'
        '"run_label":"codex-gpt-5-6-sol","harness":"codex",'
        '"model":"gpt-5.6-sol","mode":"baseline"}]'
        "</script><script>oldUi()</script>"
    )
    ui.write_text("render()")

    merge_console(console, [baseline, mcp_only], ui)

    payload = (
        console.read_text()
        .split('<script id="data" type="application/json">', maxsplit=1)[1]
        .split("</script>", maxsplit=1)[0]
    )
    cells = json.loads(payload)
    assert {cell["run_id"] for cell in cells} == {
        "task-001/baseline/codex-gpt-5-6-sol",
        "task-001/mcp_only/codex-gpt-5-6-sol",
    }


def test_console_labels_cli_as_local_plus_sgx() -> None:
    ui_path = (
        Path(__file__).resolve().parents[1]
        / "scripts"
        / "analysis"
        / "rootcause_console_ui.js"
    )
    ui = ui_path.read_text()

    assert 'cell.mode === "cli"' in ui
    assert '"ungated (local source + sgx)"' in ui


def test_console_includes_code_finder_inner_trace_and_telemetry(
    tmp_path: Path,
) -> None:
    task_dir = _make_task(tmp_path)
    run_dir = _make_run(
        tmp_path,
        harness="codex",
        model="gpt-5.6-sol",
        variant_label="codex-gpt-5-6-sol",
        mode="mcp_code_finder",
        records=[],
    )
    result_path = run_dir / "results.json"
    result = json.loads(result_path.read_text())
    result["tool_usage"]["retrieval"] = {
        "valid": True,
        "code_finder_calls": 1,
        "direct_retrieval_calls": 0,
        "inner": {
            "turns": 4,
            "tool_calls": 7,
            "total_tokens": 1400,
            "duration_ms": 1250,
        },
        "combined": {
            "total_tokens": 1525,
            "cost_usd": None,
            "cost_note": "inner cost unavailable",
        },
        "provenance": {
            "trace_version": 1,
            "trace_started_at": "2026-07-25T12:00:00Z",
            "trace_finished_at": "2026-07-25T12:00:01Z",
            "protocol_version": "2025-06-18",
            "server_info": {"name": "sourcegraph", "version": "1.2.3"},
            "tool_names": ["code_finder", "read_file"],
            "tool_inventory_sha256": "a" * 64,
            "code_finder_schema_sha256": "b" * 64,
        },
    }
    _write_json(result_path, result)
    (run_dir / "mcp_trace.jsonl").write_text(
        json.dumps(
            {
                "direction": "request",
                "method": "tools/call",
                "id": 1,
                "tool": "code_finder",
                "arguments": {"query": "Find the root cause"},
            }
        )
        + "\n"
        + json.dumps(
            {
                "direction": "response",
                "id": 1,
                "tool": "code_finder",
                "status": 200,
                "is_error": False,
                "meta": {
                    "sourcegraphToolTelemetry": {
                        "subAgentTurns": 4,
                        "subAgentToolCalls": 7,
                        "subAgentTotalTokens": 1400,
                    }
                },
            }
        )
        + "\n"
    )

    cell = build_run_cell(run_dir, task_dir)

    assert cell["retrieval"]["valid"] is True
    assert cell["retrieval"]["inner"]["turns"] == 4
    assert cell["retrieval"]["provenance"]["tool_inventory_sha256"] == "a" * 64
    finder_events = [
        event for event in cell["trace"] if event["name"] == "sourcegraph.code_finder"
    ]
    assert len(finder_events) == 2
    assert "subAgentTurns" in finder_events[1]["result"]
    assert cell["trace_sources"] == [
        str(run_dir / "agent_stdout.log"),
        str(run_dir / "mcp_trace.jsonl"),
    ]


def test_console_reads_production_mcp_telemetry_filename(tmp_path: Path) -> None:
    task_dir = _make_task(tmp_path)
    run_dir = _make_run(
        tmp_path,
        harness="claude",
        model="claude-sonnet-5",
        variant_label="claude-sonnet-5",
        mode="mcp_code_finder",
        records=[
            {
                "type": "result",
                "subtype": "success",
                "is_error": False,
                "num_turns": 1,
                "modelUsage": {},
            }
        ],
    )
    (run_dir / "mcp_telemetry.jsonl").write_text(
        json.dumps(
            {
                "direction": "request",
                "method": "tools/call",
                "id": 1,
                "tool": "code_finder",
                "arguments": {"task": "Inspect the repository"},
            }
        )
        + "\n"
    )

    cell = build_run_cell(run_dir, task_dir)

    assert cell["trace_sources"][-1] == str(run_dir / "mcp_telemetry.jsonl")
    assert any(
        event["name"] == "sourcegraph.code_finder" for event in cell["trace"]
    )


def test_console_ui_renders_code_finder_telemetry_contract() -> None:
    ui_path = (
        Path(__file__).resolve().parents[1]
        / "scripts"
        / "analysis"
        / "rootcause_console_ui.js"
    )
    ui = ui_path.read_text()

    assert "Code Finder retrieval" in ui
    assert "combined.total_tokens" in ui
    assert "inner.turns" in ui
    assert "MCP provenance" in ui
    assert "tool_inventory_sha256" in ui
    assert "code_finder_schema_sha256" in ui


def test_console_ui_renders_judge_and_lifecycle_telemetry() -> None:
    ui_path = (
        Path(__file__).resolve().parents[1]
        / "scripts"
        / "analysis"
        / "rootcause_console_ui.js"
    )
    ui = ui_path.read_text()

    assert "Judge provenance" in ui
    assert "judge.provenance" in ui
    assert "OpenCode lifecycle" in ui
    assert "lifecycle.unfinished_step" in ui
    assert "graded_artifact_written" in ui
    assert "lifecycle.canonical_answer_written" in ui
    assert "Arm gate proof" in ui
    assert "cell.arm_gate_proof" in ui


def test_console_script_runs_directly() -> None:
    repo_root = Path(__file__).resolve().parents[1]

    completed = subprocess.run(
        [
            sys.executable,
            str(repo_root / "scripts" / "analysis" / "rootcause_console.py"),
            "--help",
        ],
        cwd=repo_root,
        capture_output=True,
        text=True,
        check=False,
        env={key: value for key, value in os.environ.items() if key != "PYTHONPATH"},
    )

    assert completed.returncode == 0, completed.stderr
    assert "Merge benchmark run traces" in completed.stdout
