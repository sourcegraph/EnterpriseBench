#!/usr/bin/env python3
"""Audit of the locked N=105 MCP-vs-baseline run set (bead EnterpriseBench-dph).

READ-ONLY over results/runs. Reuses the clean-set selection logic from
aggregate_mcp_clean.py (the generator of the locked p6ux.28 numbers) and
layers three audits on top:

1. Per-run validity triage: infra errors, sandbox failures, permission
   artifacts, harness timeouts, max-turn budget censoring, malformed
   records — a disposition for every run record (cell-level and rep*)
   under results/runs.
2. Run counts per task x mode, including replicate dirs and detection of
   cell records that are byte-copies of a promoted best replicate.
3. Model manifest: driver model IDs from agent_trace.jsonl plus the full
   per-model output-token breakdown from the agent_stdout.log modelUsage
   block (catches auxiliary/subagent models the trace regex misses).

Writes the machine-readable disposition table to docs/qa/ and prints the
summary JSON to stdout.
Reproduce: python3 results/analysis/audit_locked_runset.py > /tmp/audit.json
"""
import csv
import json
import re
import statistics
import sys
from collections import Counter
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO / "results" / "analysis"))
from aggregate_mcp_clean import DELETED, MODES, load_mode, task_dirs  # noqa: E402

RUNS = REPO / "results" / "runs"
QA_DIR = REPO / "docs" / "qa"
CSV_OUT = QA_DIR / "locked_runset_dispositions.csv"
JSON_OUT = QA_DIR / "locked_runset_audit_summary.json"

MODEL_RE = re.compile(r'"model"\s*:\s*"([^"]+)"')
# modelUsage entries in the agent_stdout.log result line:
#   "claude-sonnet-4-6":{"inputTokens":4246,"outputTokens":26276,...
USAGE_RE = re.compile(r'"(claude-[a-z0-9.-]+)":\{"inputTokens":(\d+),"outputTokens":(\d+)')
MAX_TURNS_MARKERS = ("Reached maximum number of turns", '"terminal_reason":"max_turns"')
# stderr-only signatures; agent_stdout.log is the transcript and would
# false-positive on task content (several tasks are about permission errors).
STDERR_SIGNATURES = {
    "eacces": re.compile(r"\bEACCES\b|\bPermission denied\b", re.I),
    "oom": re.compile(r"\bout of memory\b|\bOOM\b|signal 9\b|\bSIGKILL\b"),
    "timeout": re.compile(r"\btimed? ?out\b|\bETIMEDOUT\b", re.I),
    "enospc": re.compile(r"\bENOSPC\b|No space left", re.I),
    "docker": re.compile(r"docker: Error|Cannot connect to the Docker daemon", re.I),
}
INFRA_PHASES = {"agent_infra_error", "verifier_infra_error"}
CSV_FIELDS = [
    "task", "mode", "rep", "run_path", "class", "status", "phase", "success",
    "failure_class", "num_turns", "task_score", "mcp_tool_calls", "max_turns_hit",
    "models", "model_output_tokens", "in_locked_aggregate", "disposition",
    "suspect_flags", "notes",
]


def trace_models(run_dir: Path) -> list[str]:
    """Distinct driver-model IDs in a run dir's agent_trace.jsonl."""
    tp = run_dir / "agent_trace.jsonl"
    if not tp.is_file():
        return []
    try:
        models = set(MODEL_RE.findall(tp.read_text(errors="replace")))
    except OSError:
        return []
    return sorted(m for m in models if m.startswith("claude"))


def scan_stdout(run_dir: Path) -> dict:
    """Max-turn censoring + per-model output tokens from the result line."""
    fp = run_dir / "agent_stdout.log"
    out: dict = {"max_turns_hit": False, "model_output_tokens": {}}
    if not fp.is_file():
        return out
    try:
        tail = fp.read_text(errors="replace")[-6000:]
    except OSError:
        return out
    out["max_turns_hit"] = any(m in tail for m in MAX_TURNS_MARKERS)
    for model, _inp, outp in USAGE_RE.findall(tail):
        out["model_output_tokens"][model] = (
            out["model_output_tokens"].get(model, 0) + int(outp)
        )
    return out


def stderr_flags(run_dir: Path) -> list[str]:
    flags = set()
    for name in ("agent_stderr.log", "agent/stderr.log", "verifier/stderr.log"):
        fp = run_dir / name
        if not fp.is_file():
            continue
        try:
            text = fp.read_text(errors="replace")
        except OSError:
            continue
        for key, rx in STDERR_SIGNATURES.items():
            if rx.search(text):
                flags.add(f"stderr:{key}:{name}")
    return sorted(flags)


def rep_dirs(cell: Path) -> list[Path]:
    return sorted(p for p in cell.glob("rep[0-9]*") if p.is_dir())


def audit_run(task: str, mode: str, run_dir: Path, rec: dict, rep: int | None) -> dict:
    """Full disposition record for one physical run record (cell or rep)."""
    raw: dict = {}
    try:
        raw = json.load(open(run_dir / "results.json"))
    except Exception:
        pass  # rec carries class=PARSE_ERROR already

    stdout_info = scan_stdout(run_dir)
    suspects: list[str] = []
    notes: list[str] = []
    cls = rec.get("class")
    if cls == "PARSE_ERROR":
        suspects.append("results.json unparseable")
    else:
        if raw.get("success") is not True and cls == "VALID":
            suspects.append(f"success={raw.get('success')!r} while classed VALID")
        if raw.get("error"):
            suspects.append(f"error field non-empty: {str(raw.get('error'))[:120]}")
        if raw.get("failure_class"):
            suspects.append(f"failure_class={raw.get('failure_class')}")
        if raw.get("phase") in INFRA_PHASES:
            suspects.append(f"phase={raw.get('phase')}")
        if stdout_info["max_turns_hit"]:
            suspects.append("budget-censored: hit max-turns limit")
        if "status" not in raw:
            notes.append("legacy schema: no status field (classed VALID by turns>0 fallback)")
        if cls == "VALID" and rec.get("score") is None:
            suspects.append("VALID but task_score is null")
        if cls == "VALID" and not rec.get("checkpoints"):
            suspects.append("VALID but no checkpoint detail")
        if cls == "VALID" and rep is None and not rep_dirs(run_dir) \
                and not (run_dir / "verifier" / "output.json").is_file():
            suspects.append("no verifier/output.json artifact")
    suspects.extend(stderr_flags(run_dir))

    return {
        "task": task,
        "mode": mode,
        "rep": rep,
        "run_path": str(run_dir.resolve().relative_to(REPO)),
        "class": cls,
        "status": raw.get("status"),
        "phase": raw.get("phase"),
        "success": raw.get("success"),
        "failure_class": raw.get("failure_class"),
        "num_turns": rec.get("num_turns"),
        "task_score": rec.get("score"),
        "mcp_tool_calls": rec.get("mcp_calls"),
        "max_turns_hit": stdout_info["max_turns_hit"],
        "models": trace_models(run_dir),
        "model_output_tokens": stdout_info["model_output_tokens"],
        "suspect_flags": suspects,
        "notes": notes,
        "raw_tool_usage": (raw.get("tool_usage") or {}),
    }


def load_rep(rep_dir: Path) -> dict | None:
    """Normalized record for one replicate dir, same shape as load_mode."""
    return load_mode(rep_dir.parent, rep_dir.name) if (rep_dir / "results.json").is_file() else None


def flag_promoted_cells(runs: list[dict]) -> None:
    """Detect cell records that are copies of a promoted best replicate.

    The p6ux.1 'aggregated baselines' were produced by copying the best of 3
    replicates to the cell level. When the paired mcp_only cell is a single
    independent run, the comparison is best-of-3 vs single-run — an
    asymmetry the headline reader needs to know about.
    """
    by_key = {(r["task"], r["mode"], r["rep"]): r for r in runs}
    for r in runs:
        if r["rep"] is not None:
            continue
        reps = [by_key[k] for k in by_key if k[0] == r["task"] and k[1] == r["mode"] and k[2] is not None]
        if not reps:
            continue
        twin = next((p for p in reps if p["raw_tool_usage"] == r["raw_tool_usage"]
                     and p["task_score"] == r["task_score"]), None)
        rep_scores = [p["task_score"] for p in reps if p["task_score"] is not None]
        if twin and rep_scores and r["task_score"] == max(rep_scores):
            r["suspect_flags"].append(
                f"cell promoted from best replicate ({twin['rep']} of {sorted(rep_scores)})")
        elif twin is None:
            r["notes"].append(
                f"cell run independent of replicates (rep scores {sorted(rep_scores)})")


def main() -> None:
    runs: list[dict] = []
    by_task: dict[str, dict] = {}
    for d in task_dirs():
        rec = {m: load_mode(d, m) for m in MODES}
        if not any(rec[m] for m in MODES):
            continue
        by_task[d.name] = rec
        for m in MODES:
            if rec[m] is None:
                continue
            cell = d / m
            runs.append(audit_run(d.name, m, cell, rec[m], rep=None))
            for i, rd in enumerate(rep_dirs(cell), 1):
                rep_rec = load_rep(rd)
                if rep_rec:
                    runs.append(audit_run(d.name, m, rd, rep_rec, rep=i))
    flag_promoted_cells(runs)

    # ---- Reconstruct the locked clean set exactly as aggregate_mcp_clean.py --
    clean, excluded, baseline_dead = [], [], []
    for name, rec in sorted(by_task.items()):
        b, m = rec["baseline"], rec["mcp_only"]
        if name in DELETED:
            excluded.append((name, "deleted"))
            continue
        if b is None:
            excluded.append((name, "baseline missing"))
            continue
        if m is None:
            excluded.append((name, "mcp_only missing"))
            continue
        if m["class"] in ("NO-OP", "INVALID"):
            excluded.append((name, f"mcp_only {m['class']}"))
            continue
        if b["class"] in ("NO-OP", "INVALID"):
            excluded.append((name, f"baseline {b['class']}"))
            continue
        if b["score"] is None or m["score"] is None:
            excluded.append((name, "null score"))
            continue
        if b["score"] == 0.0:
            baseline_dead.append(name)
            continue
        clean.append((name, b["score"], m["score"], m["score"] - b["score"]))

    clean_tasks = {t for t, *_ in clean}

    # disposition relative to the locked aggregate (cell records only; reps
    # are never read by aggregate_mcp_clean.py)
    for r in runs:
        t, m = r["task"], r["mode"]
        if r["rep"] is not None:
            r["in_locked_aggregate"] = False
            r["disposition"] = "excluded(replicate; aggregate reads cell-level record only)"
        elif t in clean_tasks and m in ("baseline", "mcp_only"):
            r["in_locked_aggregate"] = True
            r["disposition"] = "suspect" if r["suspect_flags"] else "valid"
        else:
            r["in_locked_aggregate"] = False
            if t in clean_tasks:
                reason = "hybrid mode (not part of headline pair)"
            else:
                reason = dict(excluded).get(t) or ("baseline_dead" if t in baseline_dead else "n/a")
            r["disposition"] = f"excluded({reason})"
        if not r["in_locked_aggregate"] and r["suspect_flags"]:
            r["disposition"] += "+suspect"

    def locked_stats(deltas: list[float]) -> dict:
        return {
            "n_clean": len(deltas),
            "mean_delta": statistics.mean(deltas) if deltas else None,
            "median_delta": statistics.median(deltas) if deltas else None,
            "better": sum(1 for d in deltas if d > 1e-9),
            "tie": sum(1 for d in deltas if abs(d) <= 1e-9),
            "worse": sum(1 for d in deltas if d < -1e-9),
        }

    deltas = [d for *_, d in clean]
    censored = {r["task"] for r in runs if r["in_locked_aggregate"] and r["max_turns_hit"]}
    deltas_sans_censored = [d for t, _, _, d in clean if t not in censored]

    # ---- Run counts (physical run records, incl. reps) -----------------------
    run_counts = {}
    for name in sorted(by_task):
        per_mode = {}
        for m in MODES:
            mode_runs = [r for r in runs if r["task"] == name and r["mode"] == m]
            promoted = sum(1 for r in mode_runs
                           if any(f.startswith("cell promoted") for f in r["suspect_flags"]))
            per_mode[m] = len(mode_runs) - promoted  # promoted cell = copy, not a distinct run
        run_counts[name] = {**per_mode, "total": sum(per_mode.values())}

    # ---- Model manifest ------------------------------------------------------
    driver_manifest: dict[str, Counter] = {m: Counter() for m in MODES}
    usage_manifest: dict[str, Counter] = {m: Counter() for m in MODES}
    no_trace = []
    for r in runs:
        if r["rep"] is not None:
            continue
        if r["models"]:
            for mdl in r["models"]:
                driver_manifest[r["mode"]][mdl] += 1
        else:
            no_trace.append(f"{r['task']}/{r['mode']}")
        for mdl, toks in r["model_output_tokens"].items():
            usage_manifest[r["mode"]][mdl] += toks

    for r in runs:  # raw_tool_usage was only needed for promoted-cell detection
        del r["raw_tool_usage"]

    out = {
        "n_tasks": len(by_task),
        "n_run_records": len(runs),
        "locked": locked_stats(deltas),
        "locked_sans_budget_censored": {
            "dropped_tasks": sorted(censored), **locked_stats(deltas_sans_censored)},
        "locked_suspect_runs": [r for r in runs if r.get("in_locked_aggregate") and r["suspect_flags"]],
        "excluded": excluded,
        "baseline_dead": baseline_dead,
        "run_counts": run_counts,
        "run_count_distribution": dict(Counter(v["total"] for v in run_counts.values())),
        "tasks_ge3_total": sum(1 for v in run_counts.values() if v["total"] >= 3),
        "tasks_ge3_per_condition": sum(
            1 for v in run_counts.values() if all(v[m] >= 3 for m in MODES)),
        "driver_model_manifest": {m: dict(c) for m, c in driver_manifest.items()},
        "model_output_tokens_by_mode": {m: dict(c) for m, c in usage_manifest.items()},
        "cells_without_trace": no_trace,
        "dispositions": runs,
    }

    QA_DIR.mkdir(parents=True, exist_ok=True)
    JSON_OUT.write_text(json.dumps(
        {k: v for k, v in out.items() if k != "dispositions"}, indent=2, default=str) + "\n")
    with open(CSV_OUT, "w", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=CSV_FIELDS)
        w.writeheader()
        for r in runs:
            row = {k: r[k] for k in CSV_FIELDS if k in r}
            row["models"] = ";".join(r["models"])
            row["model_output_tokens"] = json.dumps(r["model_output_tokens"], sort_keys=True)
            row["suspect_flags"] = "; ".join(r["suspect_flags"])
            row["notes"] = "; ".join(r["notes"])
            w.writerow(row)

    print(json.dumps(out, indent=2, default=str))


if __name__ == "__main__":
    import os

    os.chdir(REPO)  # aggregate_mcp_clean.task_dirs() resolves results/runs relatively
    main()
