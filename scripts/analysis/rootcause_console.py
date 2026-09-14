#!/usr/bin/env python3
"""Merge benchmark run traces into the self-contained root-cause console."""

from __future__ import annotations

import argparse
import json
import os
import re
import tempfile
from pathlib import Path
from typing import Any, Iterable, Sequence

try:
    from scripts.analysis.rootcause_console_claude import consume_claude_record
except ModuleNotFoundError as exc:
    if exc.name != "scripts":
        raise
    from rootcause_console_claude import consume_claude_record

DATA_SCRIPT_RE = re.compile(
    r'(<script id="data" type="application/json">)(.*?)(</script>)',
    re.DOTALL,
)
SECRET_PATTERNS = (
    re.compile(
        r"(?i)\b(OPENROUTER_API_KEY|OPENAI_API_KEY|ANTHROPIC_API_KEY|"
        r"SOURCEGRAPH_ACCESS_TOKEN|GITHUB_TOKEN)\s*[:=]\s*"
        r"(?:[\"'][^\"']*[\"']|[^\s]+)"
    ),
    re.compile(
        r"(?i)--(?:api[-_]?key|token|secret|password)\s+"
        r"(?:[\"'][^\"']*[\"']|[^\s]+)"
    ),
    re.compile(
        r"""(?ix)
        ["'](?:api[_-]?key|apiKey|token|access[_-]?token|
        refresh[_-]?token|id[_-]?token|secret|password)["']\s*:\s*
        (?:["'][^"']*["']|[^,}\s]+)
        """
    ),
    re.compile(r"(?i)\b(authorization\s*:\s*bearer)\s+\S+"),
    re.compile(r"\bsk-(?:or-v1-)?[A-Za-z0-9_-]{6,}\b"),
    re.compile(r"\bsgp_(?:local_)?[A-Za-z0-9]{20,}\b"),
)
SENSITIVE_KEYS = frozenset(
    {
        "apikey",
        "authorization",
        "accesstoken",
        "clientsecret",
        "refreshtoken",
        "idtoken",
        "openrouterapikey",
        "openaiapikey",
        "anthropicapikey",
        "sourcegraphaccesstoken",
        "githubtoken",
        "password",
        "secret",
    }
)


def redact(value: Any) -> Any:
    """Return a recursively redacted copy suitable for a shareable HTML file."""
    if isinstance(value, dict):
        return {
            key: (
                "[REDACTED]"
                if re.sub(r"[^a-z0-9]", "", str(key).lower()) in SENSITIVE_KEYS
                else redact(item)
            )
            for key, item in value.items()
        }
    if isinstance(value, list):
        return [redact(item) for item in value]
    if not isinstance(value, str):
        return value

    redacted = value
    for pattern in SECRET_PATTERNS:
        if pattern.groups:
            redacted = pattern.sub(r"\1=[REDACTED]", redacted)
        else:
            redacted = pattern.sub("[REDACTED]", redacted)
    return redacted


def _load_jsonl(path: Path) -> list[dict[str, Any]]:
    records: list[dict[str, Any]] = []
    for line_number, line in enumerate(path.read_text().splitlines(), start=1):
        if not line.strip():
            continue
        try:
            record = json.loads(line)
        except json.JSONDecodeError as exc:
            raise ValueError(f"{path}:{line_number}: invalid JSON event") from exc
        if not isinstance(record, dict):
            raise ValueError(f"{path}:{line_number}: event must be an object")
        records.append(record)
    return records


def _display(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, str):
        return value
    return json.dumps(value, ensure_ascii=False, sort_keys=True)


def _status(value: Any) -> str:
    status = str(value or "completed").lower()
    if status in {"completed", "success", "ok"}:
        return "ok"
    if status in {"failed", "error", "denied"}:
        return "error"
    return "pending"


def _codex_event(
    item: dict[str, Any],
) -> tuple[dict[str, Any] | None, dict[str, Any] | None]:
    item_type = item.get("type")
    if item_type == "command_execution":
        return (
            {
                "kind": "tool",
                "name": "shell",
                "status": _status(item.get("status")),
                "input": _display(item.get("command")),
                "result": _display(item.get("aggregated_output")),
            },
            None,
        )
    if item_type == "agent_message":
        return (
            {
                "kind": "message",
                "name": "agent",
                "status": "ok",
                "input": _display(item.get("text")),
                "result": "",
            },
            None,
        )
    if item_type == "mcp_tool_call":
        result = item.get("result") or item.get("error")
        return (
            {
                "kind": "tool",
                "name": f"{item.get('server', 'mcp')}.{item.get('tool', 'tool')}",
                "status": _status(item.get("status")),
                "input": _display(item.get("arguments")),
                "result": _display(result),
            },
            None,
        )
    if item_type == "file_change":
        changes = item.get("changes") if isinstance(item.get("changes"), list) else []
        paths = ", ".join(
            str(change.get("path", ""))
            for change in changes
            if isinstance(change, dict)
        )
        write = {
            "path": paths,
            "status": _status(item.get("status")),
            "content": "(content is not emitted by the Codex JSONL protocol)",
        }
        return (
            {
                "kind": "file",
                "name": "file change",
                "status": write["status"],
                "input": paths,
                "result": _display(changes),
            },
            write,
        )
    return None, None


def _opencode_tool_event(
    part: dict[str, Any],
) -> tuple[dict[str, Any], dict[str, Any] | None]:
    state = part.get("state") if isinstance(part.get("state"), dict) else {}
    tool = str(part.get("tool") or "tool")
    display_name = (
        tool.replace("sourcegraph_", "sourcegraph.", 1)
        if tool.startswith("sourcegraph_")
        else tool
    )
    tool_input = state.get("input")
    event = {
        "kind": "tool",
        "name": display_name,
        "status": _status(state.get("status")),
        "input": _display(tool_input),
        "result": _display(state.get("output")),
    }
    if tool != "write" or not isinstance(tool_input, dict):
        return event, None
    return event, {
        "path": str(tool_input.get("filePath") or tool_input.get("path") or ""),
        "status": event["status"],
        "content": _display(tool_input.get("content")),
    }


def normalize_trace(
    records: Iterable[dict[str, Any]],
) -> tuple[list[dict[str, Any]], dict[str, Any], list[dict[str, Any]]]:
    """Normalize Codex/OpenCode/Claude JSONL with provider-native units."""
    trace: list[dict[str, Any]] = []
    writes: list[dict[str, Any]] = []
    counts = _empty_activity_counts()
    providers: set[str] = set()
    pending_claude_tools: dict[str, tuple[int, int | None]] = {}

    for record in records:
        record_type = record.get("type")
        if record_type == "item.completed":
            _consume_codex_item(record, trace, writes, counts, providers)
        elif record_type == "turn.completed":
            _consume_codex_turn(record, trace, counts, providers)
        elif record_type in {"tool_use", "text", "step_finish"}:
            _consume_opencode_record(record, trace, writes, counts, providers)
        elif record_type in {"system", "assistant", "user", "result"}:
            consume_claude_record(
                record,
                trace,
                writes,
                counts,
                providers,
                pending_claude_tools,
            )

    activity = _trace_activity(counts, providers)
    return redact(trace), activity, redact(writes)


def _empty_activity_counts() -> dict[str, Any]:
    return {
        "codex_turns": 0,
        "opencode_steps": 0,
        "claude_turns": 0,
        "work_items": 0,
        "tool_uses": 0,
        "agent_messages": 0,
        "file_changes": 0,
        "model_usage": {},
        "model": "",
        "claude_code_version": "",
        "mcp_servers": [],
        "tools_exposed": [],
    }


def _consume_codex_item(
    record: dict[str, Any],
    trace: list[dict[str, Any]],
    writes: list[dict[str, Any]],
    counts: dict[str, int],
    providers: set[str],
) -> None:
    item = record.get("item")
    if not isinstance(item, dict):
        return
    providers.add("codex")
    counts["work_items"] += 1
    event, write = _codex_event(item)
    counter = {
        "command_execution": "tool_uses",
        "mcp_tool_call": "tool_uses",
        "agent_message": "agent_messages",
        "file_change": "file_changes",
    }.get(item.get("type"))
    if counter:
        counts[counter] += 1
    if event:
        trace.append(event)
    if write:
        writes.append(write)


def _consume_codex_turn(
    record: dict[str, Any],
    trace: list[dict[str, Any]],
    counts: dict[str, int],
    providers: set[str],
) -> None:
    providers.add("codex")
    counts["codex_turns"] += 1
    trace.append(
        {
            "kind": "boundary",
            "name": f"Codex turn {counts['codex_turns']}",
            "status": "ok",
            "input": "",
            "result": _display(record.get("usage")),
        }
    )


def _consume_opencode_record(
    record: dict[str, Any],
    trace: list[dict[str, Any]],
    writes: list[dict[str, Any]],
    counts: dict[str, int],
    providers: set[str],
) -> None:
    part = record.get("part")
    if not isinstance(part, dict):
        return
    handlers = {
        "tool_use": _consume_opencode_tool,
        "text": _consume_opencode_text,
        "step_finish": _consume_opencode_step,
    }
    handlers[str(record["type"])](part, trace, writes, counts, providers)


def _consume_opencode_tool(
    part: dict[str, Any],
    trace: list[dict[str, Any]],
    writes: list[dict[str, Any]],
    counts: dict[str, int],
    providers: set[str],
) -> None:
    if part.get("type") != "tool":
        return
    providers.add("opencode")
    counts["tool_uses"] += 1
    event, write = _opencode_tool_event(part)
    trace.append(event)
    if write:
        writes.append(write)
        counts["file_changes"] += 1


def _consume_opencode_text(
    part: dict[str, Any],
    trace: list[dict[str, Any]],
    _writes: list[dict[str, Any]],
    counts: dict[str, int],
    providers: set[str],
) -> None:
    if part.get("type") != "text":
        return
    providers.add("opencode")
    counts["agent_messages"] += 1
    trace.append(
        {
            "kind": "message",
            "name": "agent",
            "status": "ok",
            "input": _display(part.get("text")),
            "result": "",
        }
    )


def _consume_opencode_step(
    part: dict[str, Any],
    trace: list[dict[str, Any]],
    _writes: list[dict[str, Any]],
    counts: dict[str, int],
    providers: set[str],
) -> None:
    if part.get("type") != "step-finish":
        return
    providers.add("opencode")
    counts["opencode_steps"] += 1
    trace.append(_opencode_step_event(part, counts["opencode_steps"]))


def _opencode_step_event(part: dict[str, Any], step_number: int) -> dict[str, Any]:
    tokens = part.get("tokens") if isinstance(part.get("tokens"), dict) else {}
    cost = part.get("cost")
    summary = (
        f"reason={part.get('reason', '?')} · "
        f"in {tokens.get('input', 0)} · out {tokens.get('output', 0)}"
    )
    if isinstance(cost, (int, float)):
        summary += f" · ${cost:.6f}"
    return {
        "kind": "boundary",
        "name": f"OpenCode step {step_number}",
        "status": "ok",
        "input": "",
        "result": summary,
    }


def _trace_activity(counts: dict[str, Any], providers: set[str]) -> dict[str, Any]:
    if providers == {"codex"}:
        provider = "codex"
        unit = "turn"
        count = counts["codex_turns"]
    elif providers == {"opencode"}:
        provider = "opencode"
        unit = "step"
        count = counts["opencode_steps"]
    elif providers == {"claude"}:
        provider = "claude"
        unit = "turn"
        count = counts["claude_turns"]
    elif providers:
        provider = "mixed"
        unit = "provider event"
        count = (
            counts["codex_turns"]
            + counts["opencode_steps"]
            + counts["claude_turns"]
        )
    else:
        provider = "unknown"
        unit = "provider event"
        count = 0
    label_provider = {
        "codex": "Codex",
        "opencode": "OpenCode",
        "claude": "Claude",
    }.get(provider, provider)
    plural = "" if count == 1 else "s"
    activity = {
        "provider": provider,
        "primary_unit": unit,
        "primary_count": count,
        "label": f"{count} {label_provider} {unit}{plural}",
        "work_items": counts["work_items"],
        "tool_uses": counts["tool_uses"],
        "agent_messages": counts["agent_messages"],
        "file_changes": counts["file_changes"],
    }
    optional = {
        "model_usage": counts["model_usage"],
        "model": counts["model"],
        "claude_code_version": counts["claude_code_version"],
        "mcp_servers": counts["mcp_servers"],
        "tools_exposed": counts["tools_exposed"],
    }
    return {
        **activity,
        **{key: value for key, value in optional.items() if value},
    }


def _load_json(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text())
    if not isinstance(value, dict):
        raise ValueError(f"{path}: expected a JSON object")
    return value


def build_run_cell(run_dir: Path, task_dir: Path) -> dict[str, Any]:
    """Build one console cell from a persisted benchmark run."""
    result = _load_json(run_dir / "results.json")
    config = _load_run_config(run_dir, result)
    records = _load_jsonl(run_dir / "agent_stdout.log")
    trace, activity, writes = normalize_trace(records)
    config = _infer_provider_config(config, activity)
    trace_sources = [str(run_dir / "agent_stdout.log")]
    for mcp_trace_path in (
        run_dir / "mcp_trace.jsonl",
        run_dir / "mcp_telemetry.jsonl",
    ):
        if mcp_trace_path.exists():
            trace.extend(_normalize_mcp_trace(_load_jsonl(mcp_trace_path)))
            trace_sources.append(str(mcp_trace_path))
    return {
        **_run_identity(result, config, run_dir),
        **_run_measurements(result, config, activity),
        **_run_evidence(
            run_dir,
            task_dir,
            trace,
            writes,
            trace_sources,
            activity,
        ),
    }


def _infer_provider_config(
    config: dict[str, Any],
    activity: dict[str, Any],
) -> dict[str, Any]:
    inferred = dict(config)
    provider = activity.get("provider")
    if not inferred.get("harness") and provider in {"claude", "codex", "opencode"}:
        inferred["harness"] = provider
    if not inferred.get("model") and activity.get("model"):
        inferred["model"] = activity["model"]
    return inferred


def _normalize_mcp_trace(
    records: Iterable[dict[str, Any]],
) -> list[dict[str, Any]]:
    """Render proxy requests and aggregate response metadata as trace events."""
    events: list[dict[str, Any]] = []
    for record in records:
        tool = record.get("tool")
        if not tool:
            continue
        direction = record.get("direction")
        if direction == "request":
            events.append(
                {
                    "kind": "tool",
                    "name": f"sourcegraph.{tool}",
                    "status": "pending",
                    "input": _display(record.get("arguments")),
                    "result": "",
                }
            )
        elif direction == "response":
            events.append(
                {
                    "kind": "telemetry",
                    "name": f"sourcegraph.{tool}",
                    "status": "error" if record.get("is_error") else "ok",
                    "input": "",
                    "result": _display(record.get("meta")),
                }
            )
    return redact(events)


def _load_run_config(run_dir: Path, result: dict[str, Any]) -> dict[str, Any]:
    path = run_dir / "config.json"
    sidecar = _load_json(path) if path.exists() else {}
    embedded = result.get("config", {})
    if not isinstance(embedded, dict):
        raise ValueError(f"{run_dir / 'results.json'}: config must be an object")
    return {**sidecar, **embedded}


def _run_identity(
    result: dict[str, Any], config: dict[str, Any], run_dir: Path
) -> dict[str, Any]:
    task_id = str(result["task_id"])
    harness = str(config.get("harness", "unknown"))
    model = str(config.get("model", "unknown"))
    variant = str(config.get("variant_label") or f"{harness}-{model}")
    mode = str(config.get("mode", "baseline"))
    metadata = result.get("task_metadata", {})
    return {
        "run_id": f"{task_id}/{mode}/{variant}",
        "run_label": variant,
        "task": task_id,
        "harness": harness,
        "model": model,
        "mode": mode,
        "suite": metadata.get("suite", ""),
        "type": metadata.get("task_type", ""),
        "difficulty": metadata.get("difficulty", ""),
        "image_tag": result.get("image_tag", ""),
        "source": config.get("source", ""),
    }


def _run_measurements(
    result: dict[str, Any],
    config: dict[str, Any],
    activity: dict[str, Any],
) -> dict[str, Any]:
    tool_usage = result.get("tool_usage", {})
    scores = result.get("scores", {})
    harness = str(config.get("harness", "unknown"))
    reported_cost = tool_usage.get("cost_usd")
    cost = None if harness == "codex" and reported_cost == 0 else reported_cost
    flags = [
        flag
        for flag in (result.get("failure_class"), result.get("status"))
        if flag and flag != "invalid"
    ]
    provenance = result.get("provenance")
    nested_gate_proof = (
        provenance.get("arm_gate_proof") if isinstance(provenance, dict) else None
    )
    return {
        "phase": result.get("phase", ""),
        "success": result.get("success", False),
        "score": scores.get("task_score"),
        "failure_class": result.get("failure_class"),
        "infra_detail": result.get("error", ""),
        "cost": cost,
        "cost_note": "not reported by Codex" if cost is None else "",
        "in_tok": tool_usage.get("total_input_tokens", 0),
        "out_tok": tool_usage.get("total_output_tokens", 0),
        "turns": tool_usage.get("num_turns", 0),
        "activity": activity,
        "mcp_calls": tool_usage.get("mcp_tool_calls", 0),
        "sgx_calls": tool_usage.get("sgx_tool_calls", 0),
        "retrieval": redact(tool_usage.get("retrieval", {})),
        "lifecycle": redact(tool_usage.get("opencode_lifecycle", {})),
        "judge": {
            "requested": {
                "model": config.get("judge_model"),
                "account": config.get("judge_account"),
            },
            "provenance": redact(scores.get("judge_provenance", {})),
        },
        "arm_gate_proof": redact(result.get("arm_gate_proof") or nested_gate_proof),
        "timeout": config.get("timeout", ""),
        "verifier_timeout": config.get("verifier_timeout", ""),
        "memory_mb": config.get("memory_mb", ""),
        "timing": result.get("timing", {}),
        "checkpoints": scores.get("checkpoints", []),
        "flags": flags,
    }


def _run_evidence(
    run_dir: Path,
    task_dir: Path,
    trace: list[dict[str, Any]],
    writes: list[dict[str, Any]],
    trace_sources: list[str],
    activity: dict[str, Any],
) -> dict[str, Any]:
    used_tools = {
        str(event["name"])
        for event in trace
        if event.get("kind") == "tool" and event.get("name")
    }
    exposed_tools = {
        str(tool)
        for tool in activity.get("tools_exposed", [])
        if isinstance(tool, str)
    }
    tools = sorted(
        {
            *used_tools,
            *exposed_tools,
        }
    )
    calls = [event for event in trace if event.get("kind") == "tool"]
    return {
        "tools_exposed": tools,
        "mcp_servers": redact(activity.get("mcp_servers", [])),
        "instruction": _read_optional(task_dir / "instruction.md"),
        "trace": trace,
        "calls": calls,
        "writes": writes,
        "terminal": {},
        "ground_truth": _read_optional(task_dir / "ground_truth.json"),
        "expected_solution": _read_optional(task_dir / "expected_solution.json"),
        "trace_source": trace_sources[0],
        "trace_sources": trace_sources,
    }


def _read_optional(path: Path) -> str:
    return redact(path.read_text()) if path.exists() else ""


def _cell_identity(cell: dict[str, Any]) -> tuple[str, ...]:
    semantic_fields = ("task", "mode", "harness", "model")
    if all(cell.get(field) is not None for field in semantic_fields):
        return tuple(str(cell[field]) for field in semantic_fields)
    return ("run_id", str(cell.get("run_id", "")))


def merge_console(console: Path, new_cells: Sequence[dict], ui_script: Path) -> None:
    """Idempotently merge cells and inline the current console UI."""
    html = console.read_text()
    data_match = DATA_SCRIPT_RE.search(html)
    if not data_match:
        raise ValueError(f"{console}: missing JSON data script")
    cells = json.loads(data_match.group(2))
    if not isinstance(cells, list):
        raise ValueError(f"{console}: console data must be an array")

    replacements = {str(cell["run_id"]): redact(cell) for cell in new_cells}
    replacement_identities = {_cell_identity(cell) for cell in replacements.values()}
    merged = [
        redact(cell)
        for cell in cells
        if (
            not cell.get("run_id")
            or (
                str(cell["run_id"]) not in replacements
                and _cell_identity(cell) not in replacement_identities
            )
        )
    ]
    merged.extend(replacements.values())
    payload = json.dumps(merged, ensure_ascii=False, separators=(",", ":")).replace(
        "</", "<\\/"
    )
    html = html[: data_match.start(2)] + payload + html[data_match.end(2) :]

    tail_start = DATA_SCRIPT_RE.search(html).end()
    app_matches = list(
        re.finditer(r"<script>.*?</script>", html[tail_start:], re.DOTALL)
    )
    if not app_matches:
        raise ValueError(f"{console}: missing application script")
    app_match = app_matches[-1]
    start = tail_start + app_match.start()
    end = tail_start + app_match.end()
    html = html[:start] + f"<script>\n{ui_script.read_text()}\n</script>" + html[end:]
    _atomic_write(console, html)


def _atomic_write(path: Path, content: str) -> None:
    original_mode = path.stat().st_mode & 0o777
    with tempfile.NamedTemporaryFile(
        "w", dir=path.parent, prefix=f".{path.name}.", delete=False
    ) as handle:
        handle.write(content)
        temp_path = Path(handle.name)
    try:
        os.chmod(temp_path, original_mode)
        os.replace(temp_path, path)
    except BaseException:
        temp_path.unlink(missing_ok=True)
        raise


def _parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--console", type=Path, default=Path("rootcause_console.html"))
    parser.add_argument(
        "--ui",
        type=Path,
        default=Path(__file__).with_name("rootcause_console_ui.js"),
    )
    parser.add_argument("--task-dir", type=Path, required=True)
    parser.add_argument("--run", type=Path, action="append", required=True)
    return parser.parse_args(argv)


def main(argv: Sequence[str] | None = None) -> int:
    args = _parse_args(argv)
    cells = [build_run_cell(run_dir, args.task_dir) for run_dir in args.run]
    merge_console(args.console, cells, args.ui)
    print(f"Merged {len(cells)} run trace(s) into {args.console}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
