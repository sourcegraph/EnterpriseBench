#!/usr/bin/env python3
"""Re-verify the 37 checkpoint-masked tasks under the fixed verifiers (p6ux.25, kxt step 1).

A checkpoint is "masked" when it scored 0.0 uniformly across every real mode at run
time, so it could not separate tool-access conditions (baseline / mcp_only / hybrid).
The 5 verifier/gold fixes (p6ux.7 topo_order, .10 related_issues, .13 dead_code +
ownership, .16 camel path, .19 calibration gold) have landed; this script re-scores
the *existing* agent answers under those fixed verifiers WITHOUT re-running any agent.

Method (no container, no agent):
  1. Reconstruct each agent's written workspace artifacts (answer.json, REFACTOR_PLAN.md,
     DRIFT_REPORT.json, ...) from the captured `agent_trace.jsonl` Write tool-calls.
  2. Run the now-fixed `benchmarks/<dir>/checks/check_<cp>.sh` for each masked checkpoint
     with WORKSPACE=<reconstructed dir> and TASK_DIR=<benchmark dir>.
  3. Record BEFORE (stored results.json score) vs AFTER (re-scored) per mode.

Tasks whose answer was never captured (container EACCES / exit-127 teardown) cannot be
re-scored from trace; the script falls back to the stored results.json score (which, for
the config-drift charts + freshly-backfilled baselines, already reflects a post-fix
re-run) and flags the mode as `infra`.

Usage:
    python3 scripts/analysis/reverify_masked.py            # human table
    python3 scripts/analysis/reverify_masked.py --json     # machine-readable
"""
from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
RUNS = ROOT / "results" / "runs"
BENCH = ROOT / "benchmarks"
MODES = ["baseline", "mcp_only", "hybrid"]

# ---------------------------------------------------------------------------
# Canonical 37 masked tasks (remediation_diagnosis.md section A + aggregate_mcp_clean.py).
# Each entry: run_id -> list of masked checkpoint names to re-score.
# The 31 flagged by the live generator + the 6 it excludes as NO-OP/non-uniform
# (camel, the 3 config-drift charts, cal-sup-flask-errhandler-006, envoy-connpool).
# ---------------------------------------------------------------------------
MASKED: dict[str, list[str]] = {
    # topo_order — all 11 refactor_orchestration tasks (p6ux.7)
    "refactor-orch-001": ["topo_order"],
    "refactor-orch-002": ["topo_order"],
    "refactor-orch-003": ["topo_order"],
    "refactor-orch-004": ["topo_order"],
    "refactor-orch-005": ["topo_order"],
    "refactor-orch-006": ["topo_order"],
    "refactor-orch-007": ["topo_order"],
    "refactor-orch-008": ["topo_order"],
    "refactor-orchestration-tri-babel-001": ["topo_order"],
    "refactor-orchestration-tri-spring-001": ["topo_order"],
    "refactor-orchestration-tri-tokio-001": ["topo_order"],
    # related_issues / ownership / severity / code_paths — support_code_mapping (p6ux.10, .13)
    "support-map-firefox-css-crash-006": ["related_issues", "ownership", "severity", "code_paths"],
    "support-map-grafana-alerts-004": ["related_issues"],
    "support-map-grafana-import-003": ["related_issues"],
    "support-map-k8s-eviction-005": ["related_issues"],
    "support-map-kafka-bufpool-012": ["related_issues", "ownership", "severity", "code_paths"],
    "support-map-libreoffice-formula-007": ["related_issues"],
    "support-map-llvm-assertion-002": ["related_issues"],
    "support-map-nodebb-notif-011": ["related_issues"],
    "support-map-qutebrowser-cache-008": ["related_issues", "ownership"],
    "support-map-envoy-connpool-001": ["related_issues"],
    # dead_code (p6ux.13)
    "cal-dead-click-legacy-001": ["dead_code"],
    "cal-dead-flask-legacy-002": ["dead_code"],
    "dead-code-004": ["dead_code"],
    # source_files / symbols (p6ux.16 + java env)
    "camel-routing-arch-001": ["source_files"],
    "ccx-compliance-053": ["source_files", "symbols"],
    # expected_values / drift_points — calibration + config-drift charts (p6ux.19 + s58f)
    "cal-drift-click-envvar-002": ["expected_values", "drift_points"],
    "cal-drift-flask-config-001": ["expected_values"],
    "config-drift-consul-serf-port-001": ["expected_values", "drift_points"],
    "config-drift-redis-password-003": ["expected_values", "drift_points"],
    "config-drift-scdf-rabbitmq-002": ["expected_values", "drift_points"],
    # severity / error_source — calibration (p6ux.19)
    "cal-sup-flask-errhandler-006": ["severity"],
    "cal-err-requests-proxy-003": ["error_source"],
    "error-trace-k8s-sidecar-restart-001": ["error_source"],
    # singletons
    "monorepo-boundary-rustc-attr-009": ["impact_classification"],
    "dead-code-001": ["feature_flags"],
    "schema-evolution-007": ["schema_change"],
}


def build_id_map() -> dict[str, Path]:
    """Map task.toml `id` -> benchmark dir (active + archived)."""
    out: dict[str, Path] = {}
    for toml in BENCH.rglob("task.toml"):
        try:
            text = toml.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        tid = None
        for line in text.splitlines():
            s = line.strip()
            if s.startswith("id") and "=" in s:
                tid = s.split("=", 1)[1].strip().strip('"').strip("'")
                break
        if tid:
            out.setdefault(tid, toml.parent)
    return out


def is_archived(bench: Path | None) -> bool:
    """Archived tasks live under benchmarks/_archived/ — their checks were intentionally
    left as the retired verifiers (fixes applied to active copies only)."""
    return bench is not None and "_archived" in bench.parts


def stored_score(task: str, mode: str, cp: str):
    """Score recorded in results.json at run time (None if absent)."""
    rj = RUNS / task / mode / "results.json"
    if not rj.is_file():
        return None
    try:
        d = json.loads(rj.read_text())
    except (OSError, json.JSONDecodeError):
        return None
    for c in (d.get("scores") or {}).get("checkpoints") or []:
        if c.get("name") == cp:
            return c.get("score")
    return None


def reconstruct_workspace(trace: Path, dest: Path) -> int:
    """Materialize every /workspace Write tool-call from the agent trace into `dest`.

    Returns the number of files written. Later writes to the same path win (the
    agent's final state), matching what the in-container verifier would have seen.
    """
    if not trace.is_file():
        return 0
    written = 0
    for line in trace.read_text(errors="replace").splitlines():
        line = line.strip()
        if not line or "tool_use" not in line:
            continue
        try:
            obj = json.loads(line)
        except json.JSONDecodeError:
            continue
        msg = obj.get("message")
        content = msg.get("content") if isinstance(msg, dict) else None
        if not isinstance(content, list):
            continue
        for block in content:
            if not isinstance(block, dict) or block.get("type") != "tool_use":
                continue
            if block.get("name") not in ("Write", "create_file"):
                continue
            inp = block.get("input") or {}
            fp = inp.get("file_path") or inp.get("path")
            body = inp.get("content")
            if not fp or body is None:
                continue
            # normalize the in-container /workspace path to dest-relative
            rel = fp
            for prefix in ("/workspace/", "workspace/"):
                if rel.startswith(prefix):
                    rel = rel[len(prefix):]
                    break
            else:
                rel = rel.lstrip("/")
            target = dest / rel
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(body if isinstance(body, str) else json.dumps(body))
            written += 1
    return written


def run_check(bench_dir: Path, cp: str, workspace: Path):
    """Run check_<cp>.sh under the fixed verifier. Returns (score, reason) or (None, why)."""
    script = bench_dir / "checks" / f"check_{cp}.sh"
    if not script.is_file():
        return None, f"no check script (check_{cp}.sh)"
    env = dict(os.environ, WORKSPACE=str(workspace), TASK_DIR=str(bench_dir))
    try:
        proc = subprocess.run(
            ["bash", str(script)],
            env=env, capture_output=True, text=True, timeout=120,
        )
    except subprocess.TimeoutExpired:
        return None, "timeout"
    out = proc.stdout.strip()
    # the emitted JSON is the last non-empty stdout line
    for ln in reversed(out.splitlines()):
        ln = ln.strip()
        if ln.startswith("{"):
            try:
                d = json.loads(ln)
                return d.get("score"), (d.get("reason") or d.get("detail") or "")
            except json.JSONDecodeError:
                continue
    return None, f"unparseable output (exit {proc.returncode}): {out[:120]}"


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--json", action="store_true", help="emit machine-readable JSON")
    args = ap.parse_args()

    idmap = build_id_map()
    results = []  # per (task, cp, mode)

    for task, cps in MASKED.items():
        bench = idmap.get(task)
        task_runs = RUNS / task
        for mode in MODES:
            mode_dir = task_runs / mode
            if not mode_dir.is_dir():
                continue
            trace = mode_dir / "agent_trace.jsonl"
            with tempfile.TemporaryDirectory() as tmp:
                ws = Path(tmp)
                n_files = reconstruct_workspace(trace, ws)
                for cp in cps:
                    before = stored_score(task, mode, cp)
                    after, reason, method = None, "", ""
                    if bench is None:
                        reason, method = "no benchmark dir for id", "skip"
                    elif n_files == 0:
                        # no captured answer -> trust the stored (possibly re-run) score
                        after, reason, method = before, "no agent answer in trace", "stored"
                    else:
                        after, reason = run_check(bench, cp, ws)
                        method = "rescored"
                        if after is None:
                            after, method = before, "stored(fallback)"
                    results.append({
                        "task": task, "checkpoint": cp, "mode": mode,
                        "before": before, "after": after,
                        "method": method, "reason": reason,
                        "files_reconstructed": n_files,
                    })

    # ---- per-task unmask verdict --------------------------------------------
    tasks_summary = {}
    for task, cps in MASKED.items():
        rows = [r for r in results if r["task"] == task]
        any_after_pos = any(
            (r["after"] is not None and r["after"] > 0.0) for r in rows
        )
        modes_seen = sorted({r["mode"] for r in rows})
        tasks_summary[task] = {
            "checkpoints": cps,
            "archived": is_archived(idmap.get(task)),
            "modes": modes_seen,
            "unmasked": any_after_pos,
            "rows": rows,
        }

    unmasked_n = sum(1 for t in tasks_summary.values() if t["unmasked"])
    active = {k: v for k, v in tasks_summary.items() if not v["archived"]}
    archived = {k: v for k, v in tasks_summary.items() if v["archived"]}
    active_unmasked = sum(1 for v in active.values() if v["unmasked"])
    archived_unmasked = sum(1 for v in archived.values() if v["unmasked"])

    if args.json:
        print(json.dumps({
            "n_tasks": len(MASKED),
            "n_unmasked": unmasked_n,
            "n_active": len(active), "active_unmasked": active_unmasked,
            "n_archived": len(archived), "archived_unmasked": archived_unmasked,
            "tasks": tasks_summary,
            "rows": results,
        }, indent=2, default=str))
        return 0

    # ---- human table --------------------------------------------------------
    print(f"Re-verification of {len(MASKED)} masked tasks under fixed verifiers")
    print(f"Unmasked (>=1 masked checkpoint now >0.0 on >=1 mode): "
          f"{unmasked_n}/{len(MASKED)}")
    print(f"  active:   {active_unmasked}/{len(active)} unmasked")
    print(f"  archived: {archived_unmasked}/{len(archived)} unmasked "
          f"(out of active suite; fixes applied to active copies only)\n")
    for task in MASKED:
        ts = tasks_summary[task]
        flag = "UNMASK" if ts["unmasked"] else "still-0"
        tag = "ARCH" if ts["archived"] else "actv"
        print(f"[{flag}|{tag}] {task}  ({','.join(ts['checkpoints'])})")
        for r in ts["rows"]:
            b = "—" if r["before"] is None else f"{r['before']:.3f}"
            a = "—" if r["after"] is None else f"{r['after']:.3f}"
            print(f"    {r['mode']:9s} {r['checkpoint']:20s} "
                  f"before={b:>6} after={a:>6} [{r['method']}] {r['reason'][:60]}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
