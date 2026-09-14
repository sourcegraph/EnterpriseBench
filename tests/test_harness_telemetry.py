"""Provider-neutral telemetry extraction from agent JSON event streams."""

from __future__ import annotations

import json
from pathlib import Path

from scripts.orchestration.run_task import _extract_tool_usage


def _write_log(tmp_path: Path, records: list[dict]) -> Path:
    path = tmp_path / "agent_stdout.log"
    path.write_text("\n".join(json.dumps(record) for record in records) + "\n")
    return tmp_path


def test_extracts_codex_turn_completed_usage(tmp_path: Path) -> None:
    output_dir = _write_log(
        tmp_path,
        [
            {"type": "turn.started"},
            {
                "type": "item.completed",
                "item": {
                    "type": "command_execution",
                    "status": "completed",
                },
            },
            {
                "type": "item.completed",
                "item": {
                    "type": "agent_message",
                    "text": "Done.",
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
            {
                "type": "turn.completed",
                "usage": {
                    "input_tokens": 1200,
                    "cached_input_tokens": 200,
                    "cache_write_input_tokens": 0,
                    "output_tokens": 350,
                    "reasoning_output_tokens": 100,
                },
            },
        ],
    )

    usage = _extract_tool_usage(output_dir)

    assert usage["total_input_tokens"] == 1200
    assert usage["total_output_tokens"] == 350
    assert usage["num_turns"] == 1
    assert usage["mcp_tool_calls"] == 1
    assert usage["mcp_tool_breakdown"] == {"keyword_search": 1}
    assert usage["provider_activity"] == {
        "provider": "codex",
        "primary_unit": "turn",
        "primary_count": 1,
        "work_items": 3,
        "tool_uses": 2,
        "agent_messages": 1,
        "file_changes": 0,
    }


def test_sums_opencode_step_finish_usage_and_cost(tmp_path: Path) -> None:
    output_dir = _write_log(
        tmp_path,
        [
            {
                "type": "tool_use",
                "part": {
                    "type": "tool",
                    "tool": "bash",
                    "state": {"status": "completed"},
                },
            },
            {
                "type": "tool_use",
                "part": {
                    "type": "tool",
                    "tool": "sourcegraph_keyword_search",
                    "state": {"status": "completed"},
                },
            },
            {
                "type": "tool_use",
                "part": {
                    "type": "tool",
                    "tool": "write",
                    "state": {
                        "status": "completed",
                        "input": {"filePath": "/workspace/agent_output/answer.json"},
                    },
                },
            },
            {
                "type": "step_finish",
                "part": {
                    "type": "step-finish",
                    "cost": 0.01,
                    "tokens": {
                        "input": 1000,
                        "output": 200,
                        "reasoning": 80,
                        "cache": {"read": 100, "write": 0},
                    },
                },
            },
            {
                "type": "step_finish",
                "part": {
                    "type": "step-finish",
                    "cost": 0.02,
                    "tokens": {
                        "input": 600,
                        "output": 300,
                        "reasoning": 120,
                        "cache": {"read": 50, "write": 10},
                    },
                },
            },
            {
                "type": "text",
                "part": {"type": "text", "text": "Done."},
            },
        ],
    )

    usage = _extract_tool_usage(output_dir)

    assert usage["total_input_tokens"] == 1600
    assert usage["total_output_tokens"] == 500
    assert usage["cost_usd"] == 0.03
    assert usage["num_turns"] == 2
    assert usage["mcp_tool_calls"] == 1
    assert usage["mcp_tool_breakdown"] == {"keyword_search": 1}
    assert usage["provider_activity"] == {
        "provider": "opencode",
        "primary_unit": "step",
        "primary_count": 2,
        "work_items": 0,
        "tool_uses": 3,
        "agent_messages": 1,
        "file_changes": 1,
    }


def test_opencode_lifecycle_distinguishes_canonical_write_from_unfinished_step(
    tmp_path: Path,
) -> None:
    output_dir = _write_log(
        tmp_path,
        [
            {
                "type": "step_start",
                "timestamp": 1000,
                "part": {"type": "step-start"},
            },
            {
                "type": "tool_use",
                "timestamp": 2500,
                "part": {
                    "type": "tool",
                    "tool": "write",
                    "state": {
                        "status": "completed",
                        "input": {"filePath": "/workspace/LEGACY_REPORT.md"},
                    },
                },
            },
            {
                "type": "tool_use",
                "timestamp": 2700,
                "part": {
                    "type": "tool",
                    "tool": "write",
                    "state": {
                        "status": "completed",
                        "input": {"filePath": "/workspace/LEGACY_REPORT.md"},
                    },
                },
            },
            {
                "type": "step_finish",
                "timestamp": 3000,
                "part": {"type": "step-finish", "tokens": {}},
            },
            {
                "type": "step_start",
                "timestamp": 3500,
                "part": {"type": "step-start"},
            },
        ],
    )

    lifecycle = _extract_tool_usage(
        output_dir,
        graded_artifact_path="/workspace/LEGACY_REPORT.md",
    )["opencode_lifecycle"]

    assert lifecycle == {
        "first_event_at_ms": 1000,
        "last_event_at_ms": 3500,
        "observed_duration_ms": 2500,
        "step_starts": 2,
        "step_finishes": 1,
        "last_event_type": "step_start",
        "unfinished_step": True,
        "graded_artifact_path": "/workspace/LEGACY_REPORT.md",
        "graded_artifact_written": True,
        "graded_artifact_write_at_ms": 2500,
        "canonical_answer_written": False,
        "canonical_answer_write_at_ms": None,
        "artifact_writes": ["/workspace/LEGACY_REPORT.md"],
    }


def test_opencode_lifecycle_probes_artifact_created_by_shell(tmp_path: Path) -> None:
    output_dir = _write_log(
        tmp_path,
        [
            {
                "type": "tool_use",
                "timestamp": 1000,
                "part": {
                    "type": "tool",
                    "tool": "bash",
                    "state": {
                        "status": "completed",
                        "input": {
                            "command": "printf report > /workspace/REPORT.md"
                        },
                    },
                },
            },
            {
                "type": "step_finish",
                "timestamp": 1100,
                "part": {"type": "step-finish", "tokens": {}},
            },
        ],
    )

    lifecycle = _extract_tool_usage(
        output_dir,
        graded_artifact_path="/workspace/REPORT.md",
        graded_artifact_exists=True,
    )["opencode_lifecycle"]

    assert lifecycle["graded_artifact_written"] is True
    assert lifecycle["graded_artifact_write_at_ms"] is None
    assert lifecycle["graded_artifact_write_source"] == "filesystem_probe"


def test_malformed_provider_usage_is_ignored_instead_of_crashing(
    tmp_path: Path,
) -> None:
    output_dir = _write_log(
        tmp_path,
        [
            {
                "type": "turn.completed",
                "usage": {
                    "input_tokens": "not-a-number",
                    "output_tokens": -4,
                },
            },
            {
                "type": "step_finish",
                "part": {
                    "type": "step-finish",
                    "cost": float("nan"),
                    "tokens": {"input": True, "output": None},
                },
            },
        ],
    )

    assert _extract_tool_usage(output_dir) == {
        "total_input_tokens": 0,
        "total_output_tokens": 0,
        "cost_usd": 0.0,
        "num_turns": 2,
        "mcp_tool_calls": 0,
        "mcp_tool_breakdown": {},
        "sgx_tool_calls": 0,
        "opencode_lifecycle": {
            "first_event_at_ms": None,
            "last_event_at_ms": None,
            "observed_duration_ms": None,
            "step_starts": 0,
            "step_finishes": 1,
            "last_event_type": "step_finish",
            "unfinished_step": False,
            "graded_artifact_path": "/workspace/agent_output/answer.json",
            "graded_artifact_written": False,
            "graded_artifact_write_at_ms": None,
            "canonical_answer_written": False,
            "canonical_answer_write_at_ms": None,
            "artifact_writes": [],
        },
        "provider_activity": {
            "provider": "mixed",
            "primary_unit": "provider event",
            "primary_count": 2,
            "work_items": 0,
            "tool_uses": 0,
            "agent_messages": 0,
            "file_changes": 0,
        },
    }
