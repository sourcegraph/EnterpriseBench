#!/usr/bin/env python3
"""MCP-lift study (EnterpriseBench-323) — aggregate results + IR metrics.

Conditions:
  condA = claude-sonnet-4-6 + Sourcegraph MCP (EB mcp_only / CSB mcp-remote-direct)
  condB = claude-fable-5, no MCP            (EB baseline / CSB baseline-local-direct)

Reads:
  EB:  results/mcp_lift_study/eb/<task>/<cond>/{results.json,agent_trace.jsonl}
  CSB: ~/projects/CodeScaleBench/runs/mcp_lift_study/<bm>_<model>_<ts>/<config>/
         <task>_<hash>/<job>/<trial>/{result.json,task_metrics.json,agent/*.txt}

Emits results/mcp_lift_study/study_results.json and prints markdown tables.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

EB_ROOT = Path(__file__).resolve().parents[2]
CSB_ROOT = Path.home() / "projects" / "CodeScaleBench"
STUDY = EB_ROOT / "results" / "mcp_lift_study"

EB_TASK_DIRS = {
    "ansible-galaxy-tar-regression-prove-001": "benchmarks/incident_response/ansible-galaxy-tar-regression-prove-001",
    "refactor-orch-001": "benchmarks/technical_debt/refactor-orchestration-001",
    "monorepo-boundary-pnpm-lockfile-005": "benchmarks/feature_delivery/monorepo-boundary-005",
    "dead-code-003": "benchmarks/technical_debt/dead-code-003",
    "support-map-grafana-import-003": "benchmarks/customer_escalation/support-mapping-003",
    "api-contract-grpc-metadata-001": "benchmarks/dependency_management/api-contract-001",
    "api-contract-grpc-transport-003": "benchmarks/dependency_management/api-contract-003",
    "refactor-orchestration-tri-tokio-001": "benchmarks/technical_debt/refactor-orchestration-tri-tokio-001",
    "dep-traversal-010": "benchmarks/dependency_management/dep-traversal-010",
    "config-drift-tri-kustomize-001": "benchmarks/platform_engineering/config-drift-tri-kustomize-001",
}

CSB_TASK_DIRS = {
    "ccx-vuln-remed-014": "benchmarks/csb_org_security/ccx-vuln-remed-014",
    "ccx-vuln-remed-135": "benchmarks/csb_org_security/ccx-vuln-remed-135",
    "ccx-vuln-remed-126": "benchmarks/csb_org_security/ccx-vuln-remed-126",
    "ccx-crossorg-217": "benchmarks/csb_org_crossorg/ccx-crossorg-217",
    "ccx-crossorg-288": "benchmarks/csb_org_crossorg/ccx-crossorg-288",
    "ccx-migration-274": "benchmarks/csb_org_migration/ccx-migration-274",
    "ccx-migration-289": "benchmarks/csb_org_migration/ccx-migration-289",
    "ccx-config-trace-010": "benchmarks/csb_org_crossrepo_tracing/ccx-config-trace-010",
    "ccx-agentic-223": "benchmarks/csb_org_org/ccx-agentic-223",
    "ccx-incident-145": "benchmarks/csb_org_incident/ccx-incident-145",
}

SEARCH_TOOLS = re.compile(r"(grep|glob|search|sg_keyword|sg_nls|sg_list)", re.I)
READ_TOOLS = re.compile(r"(^read$|sg_read_file|read_file)", re.I)
# Baseline agents search/read through Bash; classify by command so condB
# search/read counts are comparable to condA's named tools.
BASH_SEARCH = re.compile(r"\b(grep|rg|find|ag|ack)\b")
BASH_READ = re.compile(r"\b(cat|head|tail|less|sed)\b")
# File-path-ish strings (must contain / and an extension). Known limitation:
# bare basenames and extensionless files (Makefile, Dockerfile) in ground
# truth are excluded from the recall denominator — matching them in
# transcripts is too ambiguous to credit.
PATHLIKE = re.compile(r"^[\w@.\-]+(?:/[\w@.\-]+)+\.\w{1,12}$")
PATH_TOKEN = re.compile(r"[\w@.\-]+(?:/[\w@.\-]+)+\.\w{1,12}")


def gt_files(task_dir: Path) -> set[str]:
    """Collect repo-relative file paths mentioned in a task's ground truth."""
    out: set[str] = set()

    def walk(o):
        if isinstance(o, dict):
            for v in o.values():
                walk(v)
        elif isinstance(o, list):
            for v in o:
                walk(v)
        elif isinstance(o, str) and PATHLIKE.match(o.strip()):
            out.add(o.strip())

    for name in ("ground_truth.json", "expected_solution.json",
                 "tests/ground_truth.json", "tests/oracle_answer.json"):
        f = task_dir / name
        if f.is_file():
            try:
                walk(json.loads(f.read_text()))
            except (json.JSONDecodeError, UnicodeDecodeError):
                pass
    return out


def trace_ir(trace_path: Path, gt: set[str]) -> dict:
    """IR metrics from a Claude Code JSONL transcript."""
    tool_calls = 0
    search_calls = 0
    read_calls = 0
    files_read: set[str] = set()
    text_chunks: list[str] = []

    if not trace_path.is_file():
        return {}
    with open(trace_path, errors="replace") as fh:
        for line in fh:
            try:
                obj = json.loads(line)
            except json.JSONDecodeError:
                continue
            msg = obj.get("message") or {}
            content = msg.get("content")
            if not isinstance(content, list):
                continue
            for block in content:
                if not isinstance(block, dict):
                    continue
                if block.get("type") == "tool_use":
                    name = block.get("name", "")
                    tool_calls += 1
                    inp = block.get("input") or {}
                    if name == "Bash":
                        cmd = str(inp.get("command", ""))
                        if BASH_SEARCH.search(cmd):
                            search_calls += 1
                        if BASH_READ.search(cmd):
                            read_calls += 1
                            files_read.update(PATH_TOKEN.findall(cmd))
                    elif name != "ToolSearch" and SEARCH_TOOLS.search(name):
                        search_calls += 1
                    path = inp.get("file_path") or inp.get("path") or ""
                    if READ_TOOLS.search(name):
                        read_calls += 1
                        if path:
                            files_read.add(str(path))
                    text_chunks.append(json.dumps(inp)[:4000])
                elif block.get("type") == "tool_result":
                    c = block.get("content")
                    if isinstance(c, str):
                        text_chunks.append(c[:20000])
                    elif isinstance(c, list):
                        for part in c:
                            if isinstance(part, dict) and isinstance(part.get("text"), str):
                                text_chunks.append(part["text"][:20000])

    blob = "\n".join(text_chunks)
    # gt_seen credits a bare-basename match anywhere in tool output — lenient
    # and symmetric across conditions, but it saturates on common basenames;
    # gt_read (explicit retrieval) is the stricter, more trustworthy metric.
    seen = sum(1 for g in gt if g in blob or g.split("/")[-1] in blob)

    def norm(p: str) -> str:
        # strip sandbox prefix: /workspace/<repo>/a/b.c -> a/b.c
        m = re.match(r"^/workspace/[^/]+/(.+)$", p)
        return m.group(1) if m else p.lstrip("/")

    normed_reads = {norm(fr) for fr in files_read}
    read = sum(
        1 for g in gt
        if any(fr.endswith(g) or g.endswith(fr) for fr in normed_reads if fr)
    )
    return {
        "tool_calls": tool_calls,
        "search_calls": search_calls,
        "read_calls": read_calls,
        "distinct_files_read": len(files_read),
        "gt_files": len(gt),
        "gt_seen": seen,
        "gt_seen_recall": round(seen / len(gt), 3) if gt else None,
        "gt_read": read,
        "gt_read_recall": round(read / len(gt), 3) if gt else None,
        "search_efficiency": round(seen / search_calls, 3) if search_calls and gt else None,
    }


def eb_rows() -> list[dict]:
    rows = []
    for task, tdir in EB_TASK_DIRS.items():
        gt = gt_files(EB_ROOT / tdir)
        for cond in ("condA", "condB"):
            rdir = STUDY / "eb" / task / cond
            rj = rdir / "results.json"
            if not rj.is_file():
                rows.append({"bench": "EB", "task": task, "cond": cond, "status": "MISSING"})
                continue
            d = json.loads(rj.read_text())
            scores = d.get("scores") or {}
            cps = scores.get("checkpoints") or []
            max_score = sum(c.get("weight", 1.0) for c in cps) or None
            tu = d.get("tool_usage") or {}
            row = {
                "bench": "EB",
                "task": task,
                "cond": cond,
                "status": d.get("status"),
                "failure_class": d.get("failure_class"),
                "num_turns": tu.get("num_turns"),
                "reward": scores.get("task_score"),
                "max_score": max_score,
                "norm_reward": round(scores.get("task_score", 0) / max_score, 3) if max_score else None,
                "checkpoints": f"{scores.get('checkpoints_passed')}/{scores.get('checkpoints_total')}",
                "wall_clock_s": round((d.get("timing") or {}).get("agent", 0), 1),
                "input_tokens": tu.get("total_input_tokens"),
                "output_tokens": tu.get("total_output_tokens"),
                "cost_usd": tu.get("cost_usd"),
                "mcp_tool_calls": tu.get("mcp_tool_calls"),
            }
            row.update(trace_ir(rdir / "agent_trace.jsonl", gt))
            rows.append(row)
    return rows


def csb_rows() -> list[dict]:
    rows = []
    base = CSB_ROOT / "runs" / "mcp_lift_study"
    # cond mapping by config dir name
    for task, tdir in CSB_TASK_DIRS.items():
        gt = gt_files(CSB_ROOT / tdir)
        for cond, cfg in (("condA", "mcp-remote-direct"), ("condB", "baseline-local-direct")):
            tm = trial_dir = None
            for p in sorted(base.glob(f"*/{cfg}/{task}_*/**/task_metrics.json"),
                            key=lambda p: p.stat().st_mtime, reverse=True):
                cand = json.loads(p.read_text())
                if cand.get("reward") is not None:
                    tm, trial_dir = cand, p.parent
                    break
            if tm is None:
                rows.append({"bench": "CSB", "task": task, "cond": cond, "status": "MISSING"})
                continue
            row = {
                "bench": "CSB",
                "task": task,
                "cond": cond,
                "status": tm.get("status"),
                "reward": tm.get("reward"),
                "max_score": 1.0,
                "norm_reward": tm.get("reward"),
                "wall_clock_s": round(tm.get("agent_execution_seconds") or tm.get("wall_clock_seconds") or 0, 1),
                "input_tokens": tm.get("input_tokens"),
                "output_tokens": tm.get("output_tokens"),
                "cache_read_tokens": tm.get("cache_read_tokens"),
                "cost_usd": tm.get("cost_usd"),
                "mcp_tool_calls": tm.get("tool_calls_mcp"),
                "tool_calls": tm.get("tool_calls_total"),
                "search_calls": (tm.get("search_calls_keyword") or 0) + (tm.get("search_calls_nls") or 0),
            }
            trace = trial_dir / "agent" / "claude-code.txt"
            ir = trace_ir(trace, gt)
            for k, v in ir.items():
                row.setdefault(k, v)
            rows.append(row)
    return rows


def fmt_table(rows: list[dict], cols: list[str]) -> str:
    head = "| " + " | ".join(cols) + " |"
    sep = "|" + "|".join("---" for _ in cols) + "|"
    body = []
    for r in rows:
        body.append("| " + " | ".join(str(r.get(c, "")) for c in cols) + " |")
    return "\n".join([head, sep] + body)


def aggregate(rows: list[dict], bench: str | None = None) -> dict | None:
    """Paired condA/condB aggregate over valid (non-INVALID, scored) pairs."""
    by_task: dict = {}
    for r in rows:
        if bench and r["bench"] != bench:
            continue
        by_task.setdefault((r["bench"], r["task"]), {})[r["cond"]] = r
    def valid(r):
        # Mirrors the locked campaign's validity rule: a run with zero turns
        # is a NO-OP (fake 0.0), not a measurement.
        return (r.get("norm_reward") is not None and r.get("status") != "INVALID"
                and r.get("num_turns", 1) != 0)

    pairs = {
        k: v for k, v in by_task.items()
        if all(c in v and valid(v[c]) for c in ("condA", "condB"))
    }
    n = len(pairs)
    if not n:
        return None

    def col(cond, key):
        return [v[cond].get(key) or 0 for v in pairs.values()]

    def mean_present(cond, key):
        vals = [v[cond].get(key) for v in pairs.values() if v[cond].get(key) is not None]
        return round(sum(vals) / len(vals), 3) if vals else None

    qa, qb = sum(col("condA", "norm_reward")), sum(col("condB", "norm_reward"))
    ca, cb = sum(col("condA", "cost_usd")), sum(col("condB", "cost_usd"))
    wins = sum(1 for v in pairs.values()
               if v["condA"]["norm_reward"] > v["condB"]["norm_reward"] + 1e-9)
    losses = sum(1 for v in pairs.values()
                 if v["condB"]["norm_reward"] > v["condA"]["norm_reward"] + 1e-9)
    return {
        "bench": bench or "ALL", "n_pairs": n,
        "mean_reward_A": round(qa / n, 3), "mean_reward_B": round(qb / n, 3),
        "delta_A_minus_B": round((qa - qb) / n, 3),
        "A_win/tie/B_win": f"{wins}/{n - wins - losses}/{losses}",
        "total_cost_A": round(ca, 2), "total_cost_B": round(cb, 2),
        "total_time_A_s": round(sum(col("condA", "wall_clock_s"))),
        "total_time_B_s": round(sum(col("condB", "wall_clock_s"))),
        "cost_per_quality_point_A": round(ca / qa, 2) if qa else None,
        "cost_per_quality_point_B": round(cb / qb, 2) if qb else None,
        "gt_seen_recall_A": mean_present("condA", "gt_seen_recall"),
        "gt_seen_recall_B": mean_present("condB", "gt_seen_recall"),
        "gt_read_recall_A": mean_present("condA", "gt_read_recall"),
        "gt_read_recall_B": mean_present("condB", "gt_read_recall"),
        "mean_tool_calls_A": mean_present("condA", "tool_calls"),
        "mean_tool_calls_B": mean_present("condB", "tool_calls"),
        "mean_search_calls_A": mean_present("condA", "search_calls"),
        "mean_search_calls_B": mean_present("condB", "search_calls"),
    }


def main():
    rows = eb_rows() + csb_rows()
    out = STUDY / "study_results.json"
    out.write_text(json.dumps(rows, indent=1))
    print(f"wrote {out} ({len(rows)} rows)\n")
    cols = ["bench", "task", "cond", "status", "norm_reward", "wall_clock_s",
            "input_tokens", "output_tokens", "cost_usd", "tool_calls",
            "search_calls", "mcp_tool_calls", "gt_seen_recall", "gt_read_recall"]
    print(fmt_table(rows, cols))
    aggs = [a for a in (aggregate(rows, b) for b in ("EB", "CSB", None)) if a]
    (STUDY / "study_aggregates.json").write_text(json.dumps(aggs, indent=1))
    print()
    print(fmt_table(aggs, list(aggs[0].keys())))


if __name__ == "__main__":
    main()
