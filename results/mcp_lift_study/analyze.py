#!/usr/bin/env python3
"""MCP-lift study (EnterpriseBench-323 + dse deepening) — aggregates + IR metrics.

EB conditions (n=1, unchanged from the phase-2 report):
  condA = claude-sonnet-4-6 + Sourcegraph MCP (mcp_only)
  condB = claude-fable-5, no MCP              (baseline)

CSB arms (EnterpriseBench-dse, n=3 per cell):
  armA = claude-sonnet-4-6 + SG-MCP   (run dir *_sonnet_*,   mcp-remote-direct)
  armB = claude-fable-5 baseline      (run dir *_claudefa_*, baseline-local-direct)
  armC = claude-sonnet-4-6 baseline   (run dir *_sonnet_*,   baseline-local-direct)
A−C isolates the MCP effect at fixed model; C−B the model effect at fixed tooling.

Reads:
  EB:  results/mcp_lift_study/eb/<task>/<cond>/{results.json,agent_trace.jsonl}
  CSB: ~/projects/CodeScaleBench/runs/mcp_lift_study/<bm>_<model>_<ts>/<config>/
         <task>_<hash>/<job>/<trial>/{result.json,task_metrics.json,agent/*.txt}
       (every valid trial is a row; `_quarantine*` dirs are skipped)

Costs are reported two ways per run: Claude-Code-logged `cost_usd`, and
`cost_usd_recomputed` from logged token counts at the authoritative per-MTok
prices below (platform.claude.com/docs/en/about-claude/pricing, 2026-06-12).
Cache writes are priced at the 1h-ephemeral rate — Claude Code uses 1h cache
(`ephemeral_1h_input_tokens` in run stdout); task_metrics does not split 5m/1h.

Emits results/mcp_lift_study/study_results.json and prints markdown tables.
"""

from __future__ import annotations

import json
import re
import statistics
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

# (model_tag_in_run_dir, harbor config) per CSB arm — see module docstring.
CSB_ARMS = {
    "armA": ("sonnet", "mcp-remote-direct"),
    "armB": ("claudefa", "baseline-local-direct"),
    "armC": ("sonnet", "baseline-local-direct"),
}
ARM_MODEL = {"armA": "claude-sonnet-4-6", "armB": "claude-fable-5",
             "armC": "claude-sonnet-4-6"}
# $/MTok: (input, output, cache_write_1h, cache_read)
PRICES = {
    "claude-sonnet-4-6": (3.0, 15.0, 6.0, 0.30),
    "claude-fable-5": (10.0, 50.0, 20.0, 1.0),
    "claude-haiku-4-5-20251001": (1.0, 5.0, 2.0, 0.10),  # EB-side auxiliary usage
}
# Per-model usage block in Claude Code's final stdout result line.
MODEL_USAGE_RE = re.compile(
    r'"(claude-[a-z0-9.-]+)":\{"inputTokens":(\d+),"outputTokens":(\d+),'
    r'"cacheReadInputTokens":(\d+),"cacheCreationInputTokens":(\d+)')


def recompute_cost_from_stdout(stdout_log: Path) -> float | None:
    """EB-side recompute: sum every model in the modelUsage block at its price.

    EB results.json carries only total input/output tokens (no cache split),
    so the recompute reads the per-model usage from the agent's stdout tail.
    """
    if not stdout_log.is_file():
        return None
    tail = stdout_log.read_text(errors="replace")[-8000:]
    total = None
    for model, inp, out, c_read, c_write in MODEL_USAGE_RE.findall(tail):
        c = recompute_cost(model, int(inp), int(out), int(c_write), int(c_read))
        if c is not None:
            total = (total or 0.0) + c
    return round(total, 4) if total is not None else None


def recompute_cost(model: str, inp, out, cache_write, cache_read) -> float | None:
    if model not in PRICES or out is None:
        return None
    p_in, p_out, p_cw, p_cr = PRICES[model]
    return round(((inp or 0) * p_in + (out or 0) * p_out + (cache_write or 0) * p_cw
                  + (cache_read or 0) * p_cr) / 1e6, 4)


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
                "cost_usd_recomputed": recompute_cost_from_stdout(rdir / "agent_stdout.log"),
                "mcp_tool_calls": tu.get("mcp_tool_calls"),
            }
            row.update(trace_ir(rdir / "agent_trace.jsonl", gt))
            rows.append(row)
    return rows


def csb_rows() -> list[dict]:
    """One row per valid CSB trial, across all three arms.

    A trial is valid iff reward is non-null, the agent produced output
    tokens, and its return code is 0 (infra-error triage; invalid trials
    are emitted with status=INVALID so exclusions stay visible).
    """
    rows = []
    base = CSB_ROOT / "runs" / "mcp_lift_study"
    for task, tdir in CSB_TASK_DIRS.items():
        gt = gt_files(CSB_ROOT / tdir)
        for arm, (model_tag, cfg) in CSB_ARMS.items():
            trials = []
            for p in sorted(base.glob(f"*_{model_tag}_*/{cfg}/{task}_*/**/task_metrics.json"),
                            key=lambda p: p.stat().st_mtime):
                if "_quarantine" in str(p):
                    continue
                tm = json.loads(p.read_text())
                if tm.get("reward") is None:
                    continue  # launch casualty: agent never produced a scored run
                trials.append((p.parent, tm))
            if not trials:
                rows.append({"bench": "CSB", "task": task, "cond": arm, "status": "MISSING"})
                continue
            for i, (trial_dir, tm) in enumerate(trials, 1):
                model = ARM_MODEL[arm]
                # Arm purity: the driver model plus haiku auxiliary/subagent
                # usage is the expected shape; any other model contaminates
                # the arm (observed: opus-4-8 in two fable-arm trials).
                usage = {}
                trace = trial_dir / "agent" / "claude-code.txt"
                if trace.is_file():
                    usage = {m: int(o) for m, _, o, *_ in
                             MODEL_USAGE_RE.findall(trace.read_text(errors="replace")[-4000:])}
                alien = sorted(m for m, o in usage.items()
                               if not m.startswith(model) and "haiku" not in m and o > 0)
                invalid = (not tm.get("output_tokens")
                           or (tm.get("agent_return_code") not in (0, None)))
                row = {
                    "bench": "CSB",
                    "task": task,
                    "cond": arm,
                    "trial": i,
                    "trial_dir": str(trial_dir.relative_to(base)),
                    "model": model,
                    "models_used": usage or None,
                    "status": ("INVALID" if invalid
                               else "MODEL_MIXED" if alien else tm.get("status")),
                    "alien_models": alien or None,
                    "agent_return_code": tm.get("agent_return_code"),
                    "reward": tm.get("reward"),
                    "max_score": 1.0,
                    "norm_reward": tm.get("reward"),
                    "wall_clock_s": round(tm.get("agent_execution_seconds") or tm.get("wall_clock_seconds") or 0, 1),
                    "input_tokens": tm.get("input_tokens"),
                    "output_tokens": tm.get("output_tokens"),
                    "cache_creation_tokens": tm.get("cache_creation_tokens"),
                    "cache_read_tokens": tm.get("cache_read_tokens"),
                    "cost_usd": tm.get("cost_usd"),
                    # Prefer the transcript's per-model usage: task_metrics token
                    # fields cover the driver model only, so a token-field
                    # recompute misses haiku subagent cost in baseline arms.
                    "cost_usd_recomputed": (
                        recompute_cost_from_stdout(trace)
                        or recompute_cost(
                            model, tm.get("input_tokens"), tm.get("output_tokens"),
                            tm.get("cache_creation_tokens"), tm.get("cache_read_tokens"))),
                    "mcp_tool_calls": tm.get("tool_calls_mcp"),
                    "tool_calls": tm.get("tool_calls_total"),
                    "search_calls": (tm.get("search_calls_keyword") or 0) + (tm.get("search_calls_nls") or 0),
                }
                for k, v in trace_ir(trace, gt).items():
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


def csb_valid(r: dict) -> bool:
    return (r.get("norm_reward") is not None
            and r.get("status") not in ("INVALID", "MODEL_MIXED"))


def csb_task_means(rows: list[dict]) -> dict:
    """(task, arm) -> per-cell mean over valid trials."""
    cells: dict = {}
    for r in rows:
        if r["bench"] == "CSB" and r.get("cond") in CSB_ARMS and csb_valid(r):
            cells.setdefault((r["task"], r["cond"]), []).append(r)
    out = {}
    for (task, arm), trials in cells.items():
        def m(key):
            vals = [t.get(key) for t in trials if t.get(key) is not None]
            return round(sum(vals) / len(vals), 4) if vals else None
        rewards = [t["norm_reward"] for t in trials if t.get("norm_reward") is not None]
        out[(task, arm)] = {
            "n": len(trials),
            "reward": m("norm_reward"),
            "reward_sd": round(statistics.stdev(rewards), 4) if len(rewards) > 1 else None,
            "cost_usd": m("cost_usd"),
            "cost_usd_recomputed": m("cost_usd_recomputed"),
            "wall_clock_s": m("wall_clock_s"),
            "tool_calls": m("tool_calls"),
            "search_calls": m("search_calls"),
            "gt_read_recall": m("gt_read_recall"),
            "gt_seen_recall": m("gt_seen_recall"),
        }
    return out


def csb_3arm(rows: list[dict]) -> dict:
    """Per-arm aggregates over tasks with all 3 arms present, plus deltas."""
    cells = csb_task_means(rows)
    tasks = sorted({t for t, _ in cells})
    complete = [t for t in tasks if all((t, a) in cells for a in CSB_ARMS)]

    def arm_agg(arm):
        rs = [cells[(t, arm)] for t in complete]
        def m(key):
            vals = [r[key] for r in rs if r[key] is not None]
            return round(sum(vals) / len(vals), 3) if vals else None
        q, c, cr = m("reward"), m("cost_usd"), m("cost_usd_recomputed")
        return {
            "model": ARM_MODEL[arm], "config": CSB_ARMS[arm][1],
            "n_tasks": len(rs), "trials": sum(r["n"] for r in rs),
            "mean_reward": q, "mean_cost_usd": c, "mean_cost_usd_recomputed": cr,
            "cost_per_quality_point": round(c / q, 2) if q and c else None,
            "cost_per_quality_point_recomputed": round(cr / q, 2) if q and cr else None,
            "mean_wall_clock_s": m("wall_clock_s"),
            "mean_tool_calls": m("tool_calls"), "mean_search_calls": m("search_calls"),
            "gt_read_recall": m("gt_read_recall"), "gt_seen_recall": m("gt_seen_recall"),
        }

    def pairwise(a1, a2):
        d = [cells[(t, a1)]["reward"] - cells[(t, a2)]["reward"] for t in complete]
        wins = sum(1 for x in d if x > 1e-9)
        losses = sum(1 for x in d if x < -1e-9)
        return {
            "mean_delta": round(sum(d) / len(d), 3) if d else None,
            "win/tie/loss": f"{wins}/{len(d) - wins - losses}/{losses}",
        }

    return {
        "complete_tasks": complete,
        "arms": {a: arm_agg(a) for a in CSB_ARMS},
        "mcp_effect_A_minus_C": pairwise("armA", "armC"),
        "model_effect_C_minus_B": pairwise("armC", "armB"),
        "deployment_A_minus_B": pairwise("armA", "armB"),
        "per_task": {t: {a: cells[(t, a)] for a in CSB_ARMS if (t, a) in cells}
                     for t in tasks},
    }


def main():
    rows = eb_rows() + csb_rows()
    out = STUDY / "study_results.json"
    out.write_text(json.dumps(rows, indent=1))
    print(f"wrote {out} ({len(rows)} rows)\n")
    cols = ["bench", "task", "cond", "trial", "status", "norm_reward", "wall_clock_s",
            "input_tokens", "output_tokens", "cost_usd", "cost_usd_recomputed",
            "tool_calls", "search_calls", "mcp_tool_calls", "gt_seen_recall",
            "gt_read_recall"]
    print(fmt_table(rows, cols))
    aggs = {"eb_paired": aggregate(rows, "EB"), "csb_3arm": csb_3arm(rows)}
    (STUDY / "study_aggregates.json").write_text(json.dumps(aggs, indent=1))
    print()
    arm_rows = [{"arm": a, **v} for a, v in aggs["csb_3arm"]["arms"].items()]
    print(fmt_table(arm_rows, list(arm_rows[0].keys())))
    for k in ("mcp_effect_A_minus_C", "model_effect_C_minus_B", "deployment_A_minus_B"):
        print(k, aggs["csb_3arm"][k])


if __name__ == "__main__":
    main()
