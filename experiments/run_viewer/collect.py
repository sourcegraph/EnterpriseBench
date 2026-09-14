"""Collect every scored run under results/runs/ into one index record per run.

The viewer is a single static file, so this is the only place that knows the
on-disk layout: results/runs/<task_id>/<mode>--<variant_label>/. A run without
results.json has not been scored and is left out; a run whose results.json is
unreadable is a real problem and raises rather than being silently dropped.
"""
import json
import os

MCP_TOOLS = {"code_finder", "keyword_search", "nls_search", "read_file",
             "list_files", "commit_search", "diff_search", "repo_metadata"}
SKIP_DIRS = {"_invalidated", "_archived"}
SEQUENCE_HEAD = 8


def _scan_trace(run_dir):
    """Opening tool calls and total context tokens, from the trace every run writes.

    Two reasons this cannot come from the easier sources:

    Only the treatment arm of an A/B goes through the MCP telemetry proxy, so
    mcp_trace.jsonl exists on one side and not the other. agent_trace.jsonl is
    written for every run, which is what makes two arms comparable.

    And a Claude harness reports total_input_tokens EXCLUDING cache reads, while
    a Codex harness reports cumulative input across turns. Put side by side in
    one column those differ by two orders of magnitude for no real reason, so
    context is summed here as input + cache_creation + cache_read, deduped by
    message id because a streaming trace repeats a message as it grows.
    """
    path = os.path.join(run_dir, "agent_trace.jsonl")
    if not os.path.exists(path):
        return None, [], None
    head, context, seen = [], 0, set()
    with open(path, errors="replace") as fh:
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
            usage = message.get("usage")
            msg_id = message.get("id")
            if isinstance(usage, dict) and msg_id and msg_id not in seen:
                seen.add(msg_id)
                context += (usage.get("input_tokens") or 0)
                context += (usage.get("cache_creation_input_tokens") or 0)
                context += (usage.get("cache_read_input_tokens") or 0)
            if len(head) < SEQUENCE_HEAD:
                for block in message.get("content") or []:
                    if not isinstance(block, dict) or block.get("type") != "tool_use":
                        continue
                    name = block.get("name") or ""
                    if name.startswith("mcp__"):
                        name = name.split("__")[-1]
                    if len(head) < SEQUENCE_HEAD:
                        head.append(name)
    mcp = [t for t in head if t in MCP_TOOLS]
    return (mcp[0] if mcp else None), head, (context or None)


def _context(traced, reported):
    """Tokens the model read, and where the figure came from.

    Both candidates are lower bounds on the real context, so the larger of the
    two is the tightest one available. They disagree in both directions and for
    different reasons: a Claude harness reports input EXCLUDING cache reads, so
    reported is the smaller by two or three orders of magnitude on almost every
    run; and a handful of traces were captured only in part, so the trace sum is
    the one that undercounts there. Guessing per harness would get those wrong,
    and quietly picking the bigger number without saying so would hide a real
    data problem, which is why the source travels with the value.
    """
    if not traced:
        return (reported, "reported") if reported else (None, "")
    if not reported or traced >= reported:
        return traced, "trace"
    return reported, "reported (trace is incomplete)"


def _normalized(scores):
    """Task score on a 0-1 scale, and whether it had to be rescaled to get there.

    Runs written before the scorer switched to a weighted mean carry a weighted
    SUM: three checkpoints all passing reads 3.0, not 1.0. Averaging those in
    with current runs would put the mean score above 1. Dividing by the total
    weight recovers the comparable number, and the raw value stays on the record
    so the rescaling is visible rather than assumed.
    """
    raw = scores.get("task_score")
    if not isinstance(raw, (int, float)):
        return None, False
    if raw <= 1.0:
        return raw, False
    weight = sum(c.get("weight") or 0 for c in scores.get("checkpoints") or [])
    if weight <= 0:
        weight = scores.get("checkpoints_total") or 0
    if weight <= 0:
        return None, False
    return raw / weight, True


def read_run(run_dir):
    with open(os.path.join(run_dir, "results.json")) as fh:
        results = json.load(fh)
    config = {}
    config_path = os.path.join(run_dir, "config.json")
    if os.path.exists(config_path):
        with open(config_path) as fh:
            config = json.load(fh)

    scores = results.get("scores") or {}
    usage = results.get("tool_usage") or {}
    meta = results.get("task_metadata") or {}
    timing = results.get("timing") or {}
    breakdown = usage.get("mcp_tool_breakdown") or {}
    variant = os.path.basename(run_dir)
    mode, _, label = variant.partition("--")
    first, head, context = _scan_trace(run_dir)
    score, rescaled = _normalized(scores)
    ctx_value, ctx_source = _context(context, usage.get("total_input_tokens"))

    return {
        "task": os.path.basename(os.path.dirname(run_dir)),
        "variant": variant,
        "mode": config.get("mode") or mode,
        "label": config.get("variant_label") or label or "",
        "model": config.get("model") or "",
        "harness": config.get("harness") or "",
        "suite": meta.get("suite") or "",
        "type": meta.get("task_type") or "",
        "difficulty": meta.get("difficulty") or "",
        "languages": meta.get("languages") or [],
        "repos": scores.get("repos") or [],
        "score": score,
        "raw_score": scores.get("task_score"),
        "rescaled": rescaled,
        "passed": scores.get("checkpoints_passed"),
        "total": scores.get("checkpoints_total"),
        "checkpoints": [
            {"name": c.get("name"), "score": c.get("score"),
             "weight": c.get("weight"), "passed": bool(c.get("passed"))}
            for c in scores.get("checkpoints") or []
        ],
        "ok": bool(results.get("success")),
        "phase": results.get("phase") or "",
        "failure": results.get("failure_class") or "",
        "error": (results.get("error") or "")[:400],
        "context": ctx_value,
        "context_from": ctx_source,
        "input": usage.get("total_input_tokens"),
        "output": usage.get("total_output_tokens"),
        "cost": usage.get("cost_usd"),
        "turns": usage.get("num_turns"),
        "mcp": usage.get("mcp_tool_calls"),
        "cf": breakdown.get("code_finder", 0),
        "tools": {k: v for k, v in sorted(breakdown.items())},
        "wall": round(sum(v for v in timing.values() if isinstance(v, (int, float))), 1),
        "first": first,
        "head": head,
    }


def collect(runs_root):
    """Every scored run under runs_root, ordered by task then variant."""
    if not os.path.isdir(runs_root):
        raise NotADirectoryError(runs_root)
    out = []
    for task in sorted(os.listdir(runs_root)):
        if task in SKIP_DIRS or task.startswith("."):
            continue
        task_dir = os.path.join(runs_root, task)
        if not os.path.isdir(task_dir):
            continue
        for variant in sorted(os.listdir(task_dir)):
            run_dir = os.path.join(task_dir, variant)
            if os.path.exists(os.path.join(run_dir, "results.json")):
                out.append(read_run(run_dir))
    return out
