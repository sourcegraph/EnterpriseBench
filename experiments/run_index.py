"""Read one benchmark run directory into a plain record.

Both arms of the tool-description A/B must be read the same way. Only the
treatment arm went through the MCP telemetry proxy, so mcp_trace.jsonl exists
on one side and not the other; anything sourced from it would be comparing two
different instruments. agent_trace.jsonl is written for every run, so the tool
sequence comes from there for both.
"""
import json
import os

MCP_PREFIX = "mcp__"


def tool_sequence(run_dir):
    """Ordered tool names the agent actually invoked, MCP names unprefixed."""
    path = os.path.join(run_dir, "agent_trace.jsonl")
    if not os.path.exists(path):
        raise FileNotFoundError(path)
    seq = []
    with open(path) as fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            try:
                event = json.loads(line)
            except json.JSONDecodeError:
                continue
            message = event.get("message")
            if not isinstance(message, dict):
                continue
            content = message.get("content")
            if not isinstance(content, list):
                continue
            for block in content:
                if isinstance(block, dict) and block.get("type") == "tool_use":
                    name = block.get("name") or ""
                    if name.startswith(MCP_PREFIX):
                        name = name.split("__")[-1]
                    seq.append(name)
    return seq


def read_run(run_dir):
    """One run as a record. Raises if results.json is missing or unparseable."""
    with open(os.path.join(run_dir, "results.json")) as fh:
        results = json.load(fh)
    usage = results.get("tool_usage") or {}
    breakdown = usage.get("mcp_tool_breakdown") or {}
    seq = tool_sequence(run_dir)
    mcp_seq = [t for t in seq if t in breakdown or t in MCP_TOOLS]
    return {
        "task": os.path.basename(os.path.dirname(run_dir)),
        "variant": os.path.basename(run_dir),
        "score": (results.get("scores") or {}).get("task_score"),
        "status": results.get("status"),
        "input_tokens": usage.get("total_input_tokens"),
        "output_tokens": usage.get("total_output_tokens"),
        "cost_usd": usage.get("cost_usd"),
        "tool_calls": usage.get("total_tool_calls"),
        "mcp_calls": usage.get("mcp_tool_calls"),
        "code_finder_calls": breakdown.get("code_finder", 0),
        "breakdown": dict(breakdown),
        "first_mcp_tool": mcp_seq[0] if mcp_seq else None,
        "sequence": seq,
    }


MCP_TOOLS = {"code_finder", "keyword_search", "nls_search", "read_file",
             "list_files", "commit_search", "diff_search", "repo_metadata",
             "get_file_contents", "search_code"}
