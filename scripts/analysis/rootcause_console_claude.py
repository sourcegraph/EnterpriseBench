"""Normalize Claude Code stream-JSON records for the root-cause console."""

from __future__ import annotations

import json
from typing import Any


def consume_claude_record(
    record: dict[str, Any],
    trace: list[dict[str, Any]],
    writes: list[dict[str, Any]],
    counts: dict[str, Any],
    providers: set[str],
    pending_tools: dict[str, tuple[int, int | None]],
) -> None:
    """Add one Claude stream-JSON record to the normalized console trace."""
    providers.add("claude")
    record_type = record.get("type")
    if record_type == "system" and record.get("subtype") == "init":
        _consume_init(record, trace, counts)
    elif record_type == "assistant":
        _consume_assistant(record, trace, writes, counts, pending_tools)
    elif record_type == "user":
        _consume_tool_results(record, trace, writes, pending_tools)
    elif record_type == "result":
        _consume_result(record, trace, counts)


def _consume_init(
    record: dict[str, Any],
    trace: list[dict[str, Any]],
    counts: dict[str, Any],
) -> None:
    counts["model"] = str(record.get("model") or "")
    counts["claude_code_version"] = str(record.get("claude_code_version") or "")
    counts["mcp_servers"] = record.get("mcp_servers") or []
    counts["tools_exposed"] = record.get("tools") or []
    trace.append(
        {
            "kind": "boundary",
            "name": "Claude session init",
            "status": "ok",
            "input": "",
            "result": _display(
                {
                    "model": counts["model"],
                    "claude_code_version": counts["claude_code_version"],
                    "mcp_servers": counts["mcp_servers"],
                }
            ),
        }
    )


def _consume_assistant(
    record: dict[str, Any],
    trace: list[dict[str, Any]],
    writes: list[dict[str, Any]],
    counts: dict[str, Any],
    pending_tools: dict[str, tuple[int, int | None]],
) -> None:
    message = record.get("message")
    content = message.get("content") if isinstance(message, dict) else None
    if not isinstance(content, list):
        return
    for block in content:
        if not isinstance(block, dict):
            continue
        block_type = block.get("type")
        if block_type == "thinking":
            trace.append(_text_event("thinking", block.get("thinking")))
        elif block_type == "text":
            trace.append(_text_event("message", block.get("text")))
            counts["agent_messages"] += 1
        elif block_type == "tool_use":
            _consume_tool_use(block, trace, writes, counts, pending_tools)


def _text_event(kind: str, value: Any) -> dict[str, Any]:
    return {
        "kind": kind,
        "name": "thinking" if kind == "thinking" else "agent",
        "status": "ok",
        "input": _display(value),
        "result": "",
    }


def _consume_tool_use(
    block: dict[str, Any],
    trace: list[dict[str, Any]],
    writes: list[dict[str, Any]],
    counts: dict[str, Any],
    pending_tools: dict[str, tuple[int, int | None]],
) -> None:
    tool_input = block.get("input")
    tool_name = str(block.get("name") or "tool")
    event = {
        "kind": "tool",
        "name": _tool_name(tool_name),
        "status": "pending",
        "input": _display(tool_input),
        "result": "",
    }
    trace.append(event)
    write_index = _record_write(tool_name, tool_input, writes, counts)
    tool_id = block.get("id")
    if isinstance(tool_id, str):
        pending_tools[tool_id] = (len(trace) - 1, write_index)
    counts["tool_uses"] += 1


def _record_write(
    tool_name: str,
    tool_input: Any,
    writes: list[dict[str, Any]],
    counts: dict[str, Any],
) -> int | None:
    if tool_name not in {"Write", "Edit"} or not isinstance(tool_input, dict):
        return None
    write_index = len(writes)
    writes.append(
        {
            "path": str(tool_input.get("file_path") or ""),
            "status": "ok",
            "content": _display(
                tool_input.get("content") or tool_input.get("new_string")
            ),
        }
    )
    counts["file_changes"] += 1
    return write_index


def _tool_name(name: str) -> str:
    if name.startswith("mcp__"):
        parts = name.split("__", 2)
        if len(parts) == 3:
            return f"{parts[1]}.{parts[2]}"
    return name


def _consume_tool_results(
    record: dict[str, Any],
    trace: list[dict[str, Any]],
    writes: list[dict[str, Any]],
    pending_tools: dict[str, tuple[int, int | None]],
) -> None:
    message = record.get("message")
    content = message.get("content") if isinstance(message, dict) else None
    if not isinstance(content, list):
        return
    for block in content:
        if not isinstance(block, dict) or block.get("type") != "tool_result":
            continue
        pending = pending_tools.pop(str(block.get("tool_use_id") or ""), None)
        if pending is None:
            continue
        trace_index, write_index = pending
        status = "error" if block.get("is_error") else "ok"
        trace[trace_index] = {
            **trace[trace_index],
            "status": status,
            "result": _display(block.get("content")),
        }
        if write_index is not None:
            writes[write_index] = {**writes[write_index], "status": status}


def _consume_result(
    record: dict[str, Any],
    trace: list[dict[str, Any]],
    counts: dict[str, Any],
) -> None:
    turns = record.get("num_turns")
    if isinstance(turns, int) and turns >= 0:
        counts["claude_turns"] = turns
    model_usage = record.get("modelUsage")
    if isinstance(model_usage, dict):
        counts["model_usage"] = model_usage
    trace.append(
        {
            "kind": "boundary",
            "name": "Claude result",
            "status": "error" if record.get("is_error") else "ok",
            "input": "",
            "result": _display(
                {
                    "subtype": record.get("subtype"),
                    "num_turns": counts["claude_turns"],
                    "total_cost_usd": record.get("total_cost_usd"),
                    "modelUsage": counts["model_usage"],
                }
            ),
        }
    )


def _display(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, str):
        return value
    return json.dumps(value, ensure_ascii=False, sort_keys=True)
