#!/usr/bin/env python3
"""Validity-filtered MCP-vs-baseline aggregation (bead EnterpriseBench-klp, campaign p6ux).

READ-ONLY: reads results/runs/<task>/{baseline,mcp_only,hybrid}/results.json.
Classifies validity from the 'status' field + num_turns; uses mcp_calls>0 as the
MCP-usage ground truth (mcp_handshake_ok is a confirmed false-negative, ignored).
"""
import json
import os
import statistics
from pathlib import Path

RUNS = Path("results/runs")
MODES = ["baseline", "mcp_only", "hybrid"]
DELETED = {"aspnetcore-code-review-001", "beam-pipeline-builder-refac-001",
           "ansible-abc-imports-fix-001"}  # cut in commit 9fa129f (coding-ability tasks)


def load_mode(task_dir: Path, mode: str):
    """Return a normalized record for one (task, mode), or None if no results.json."""
    fp = task_dir / mode / "results.json"
    if not fp.is_file():
        return None
    try:
        d = json.load(open(fp))
    except Exception as e:
        return {"present": True, "parse_error": str(e), "class": "PARSE_ERROR"}
    scores = d.get("scores") or {}
    tool = d.get("tool_usage") or {}
    score = scores.get("task_score")
    num_turns = tool.get("num_turns")
    mcp_calls = tool.get("mcp_tool_calls")
    status = d.get("status")  # VALID / INVALID / FALLBACK / None (old runs lack it)
    checkpoints = scores.get("checkpoints") or []
    # Derive validity class.
    if num_turns in (0, None):
        cls = "NO-OP"
    elif status in ("VALID", "INVALID", "FALLBACK"):
        cls = status
    else:
        cls = "VALID"  # real run (turns>0), legacy schema without status field
    return {
        "present": True,
        "score": score,
        "num_turns": num_turns,
        "mcp_calls": mcp_calls or 0,
        "mcp_used": bool(mcp_calls and mcp_calls > 0),
        "status": status,
        "class": cls,
        "checkpoints": checkpoints,
    }


def task_dirs():
    out = []
    for d in sorted(RUNS.iterdir()):
        if not d.is_dir():
            continue
        name = d.name
        if name.startswith("_") or name[:8].isdigit():
            continue
        out.append(d)
    return out


def main():
    tasks = {}
    for d in task_dirs():
        rec = {m: load_mode(d, m) for m in MODES}
        if any(rec[m] for m in MODES):
            tasks[d.name] = rec

    # ---- Build comparison rows -------------------------------------------------
    rows = []          # full per-task detail
    for name, rec in sorted(tasks.items()):
        b, m, h = rec["baseline"], rec["mcp_only"], rec["hybrid"]
        rows.append({
            "task": name,
            "deleted": name in DELETED,
            "baseline": b,
            "mcp_only": m,
            "hybrid": h,
        })

    # ---- MCP-vs-baseline clean set --------------------------------------------
    # Include iff: not deleted, baseline VALID (real, scored), mcp_only VALID
    # (exclude INVALID/NO-OP/missing). Record baseline-dead separately.
    clean = []         # (task, baseline_score, mcp_score, delta)
    excluded = []      # (task, reason)
    baseline_dead = [] # baseline also 0/dead but mcp ran
    for r in rows:
        t = r["task"]
        b, m = r["baseline"], r["mcp_only"]
        if r["deleted"]:
            excluded.append((t, "deleted (cut in 9fa129f)"))
            continue
        if b is None or not b.get("present"):
            excluded.append((t, "baseline missing"))
            continue
        if m is None or not m.get("present"):
            excluded.append((t, "mcp_only missing"))
            continue
        if m["class"] in ("NO-OP", "INVALID"):
            excluded.append((t, f"mcp_only {m['class']}"))
            continue
        if b["class"] in ("NO-OP", "INVALID"):
            excluded.append((t, f"baseline {b['class']}"))
            continue
        bs, ms = b["score"], m["score"]
        if bs is None or ms is None:
            excluded.append((t, "null score"))
            continue
        # baseline genuinely dead (0.0) — note separately, keep in clean too? -> separate.
        if bs == 0.0:
            baseline_dead.append((t, bs, ms, ms - bs))
            continue
        clean.append((t, bs, ms, ms - bs))

    deltas = [d for _, _, _, d in clean]
    better = sum(1 for d in deltas if d > 1e-9)
    worse = sum(1 for d in deltas if d < -1e-9)
    tie = sum(1 for d in deltas if abs(d) <= 1e-9)

    # ---- Hybrid: did it actually use MCP? -------------------------------------
    hybrid_real = [r for r in rows if not r["deleted"] and r["hybrid"]
                   and r["hybrid"].get("present")
                   and r["hybrid"]["class"] not in ("NO-OP", "INVALID")]
    hybrid_used_mcp = [r for r in hybrid_real if r["hybrid"]["mcp_used"]]
    hybrid_fallback = [r for r in hybrid_real if not r["hybrid"]["mcp_used"]]

    # ---- Checkpoint-masking: uniform-0.0 checkpoint across all 3 modes ---------
    masked = []  # (task, [checkpoint names])
    for r in rows:
        if r["deleted"]:
            continue
        modes_present = [r[mm] for mm in MODES if r[mm] and r[mm].get("present")
                         and r[mm]["class"] not in ("NO-OP", "INVALID")
                         and r[mm].get("checkpoints")]
        if len(modes_present) < 2:  # need >=2 real modes to call it "uniform"
            continue
        # map checkpoint name -> list of scores across modes
        cp_names = None
        ok = True
        for md in modes_present:
            names = tuple(c["name"] for c in md["checkpoints"])
            if cp_names is None:
                cp_names = names
            elif names != cp_names:
                ok = False
                break
        if not ok or not cp_names:
            continue
        uniform_zero = []
        for i, cpn in enumerate(cp_names):
            vals = [md["checkpoints"][i]["score"] for md in modes_present]
            if all(v == 0.0 for v in vals):
                uniform_zero.append(cpn)
        if uniform_zero:
            masked.append((r["task"], uniform_zero, len(modes_present)))

    # ---- Emit summary object ---------------------------------------------------
    summary = {
        "n_tasks_with_results": len(rows),
        "clean": clean,
        "excluded": excluded,
        "baseline_dead": baseline_dead,
        "n_clean": len(clean),
        "delta_mean": statistics.mean(deltas) if deltas else None,
        "delta_median": statistics.median(deltas) if deltas else None,
        "mcp_better": better,
        "mcp_tie": tie,
        "mcp_worse": worse,
        "hybrid_n_real": len(hybrid_real),
        "hybrid_used_mcp": len(hybrid_used_mcp),
        "hybrid_fallback": len(hybrid_fallback),
        "hybrid_used_mcp_tasks": [r["task"] for r in hybrid_used_mcp],
        "masked": masked,
    }
    print(json.dumps(summary, indent=2, default=str))


if __name__ == "__main__":
    os.chdir(Path(__file__).resolve().parents[2])
    main()
