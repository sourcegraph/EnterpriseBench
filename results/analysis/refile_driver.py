#!/usr/bin/env python3
"""Sequential refile driver for bead EnterpriseBench-69ix (campaign p6ux, batch 2).

Re-runs baseline + mcp_only + hybrid for the 13 remaining perm-bug tasks
post-s58f-fix, one container at a time (disk is tight). Unlike batch 1 (10
asymmetric tasks where only the MCP modes were fake-0), batch 2 covers tasks
where the perm bug also killed baseline: 7 'both-dead' (baseline ALSO fake-0
NO-OP) + 6 'baseline-missing' (baseline never ran) — so ALL 3 modes are run.

(aspnetcore-code-review-001 was in the stale audit but is excluded: it was
deleted in commit 9fa129f as a coding-ability task; its task.toml no longer
exists. Runnable batch-2 set = 13 tasks.)

Idempotent/resumable: a mode whose results.json already has a non-null `status`
(i.e. a post-fix run) is NOT re-run — its row is just (re)emitted to the
before/after log.

STOP conditions (write STOP file + exit non-zero, so the agent mails project-lead):
  * results.json missing after a run
  * a run still records status==INVALID or num_turns==0 (perm fix did not take)
  * disk < 12G even after `docker image prune -f`
"""
from __future__ import annotations

import json
import shutil
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path("/home/ds/projects/EnterpriseBench")
ANALYSIS = ROOT / "results/analysis"
LOG = ANALYSIS / "refile_s58f_before_after.md"
STOP = ANALYSIS / "refile_s58f_STOP.txt"
PROG = ANALYSIS / "refile_s58f_progress.log"
LOGDIR = ANALYSIS / "refile_logs"
AGENT = (
    "claude --dangerously-skip-permissions --max-turns 50 "
    "--verbose --output-format stream-json -p"
)
ACCTS = [1, 3, 4]

# (task_id, task.toml). task_id MUST equal the parsed toml `id` (verified) so
# the resume/STOP checks read the right results/runs/<id>/<mode>/results.json.
# 7 both-dead first, then 6 baseline-missing.
MODES = ("baseline", "mcp_only", "hybrid")
TASKS: list[tuple[str, str]] = [
    # --- both-dead (baseline also fake-0 NO-OP) ---
    ("dead-code-004", "benchmarks/_archived/technical_debt/dead-code-004/task.toml"),
    ("dep-traversal-002", "benchmarks/dependency_management/dep-traversal-002/task.toml"),
    ("monorepo-boundary-babel-decorators-002", "benchmarks/feature_delivery/monorepo-boundary-002/task.toml"),
    ("monorepo-boundary-babel-forof-004", "benchmarks/feature_delivery/monorepo-boundary-004/task.toml"),
    ("monorepo-boundary-babel-metadata-003", "benchmarks/feature_delivery/monorepo-boundary-003/task.toml"),
    ("monorepo-boundary-babel-ts-init-001", "benchmarks/feature_delivery/monorepo-boundary-001/task.toml"),
    ("schema-evolution-008", "benchmarks/_archived/feature_delivery/schema-evolution-008/task.toml"),
    # --- baseline-missing (baseline never ran) ---
    ("dead-code-001", "benchmarks/technical_debt/dead-code-001/task.toml"),
    ("dead-code-002", "benchmarks/technical_debt/dead-code-002/task.toml"),
    ("dep-traversal-001", "benchmarks/dependency_management/dep-traversal-001/task.toml"),
    ("monorepo-boundary-pnpm-lockfile-005", "benchmarks/feature_delivery/monorepo-boundary-005/task.toml"),
    ("refactor-orch-005", "benchmarks/technical_debt/refactor-orchestration-005/task.toml"),
    ("schema-evolution-005", "benchmarks/_archived/feature_delivery/schema-evolution-005/task.toml"),
]


def prog(msg: str) -> None:
    line = f"{time.strftime('%H:%M:%S')} {msg}"
    with open(PROG, "a") as f:
        f.write(line + "\n")
    print(line, flush=True)


def free_gb() -> float:
    return shutil.disk_usage("/").free / (1024**3)


def stop(reason: str) -> None:
    STOP.write_text(f"{time.strftime('%Y-%m-%d %H:%M:%S')} STOP: {reason}\n")
    prog(f"STOP: {reason}")


def summarize(d: dict) -> dict:
    tu = d.get("tool_usage", {})
    return {
        "status": d.get("status"),
        "score": d.get("scores", {}).get("task_score"),
        "turns": tu.get("num_turns"),
        "mcp": tu.get("mcp_tool_calls"),
        "handshake": d.get("mcp_handshake_ok"),
        "account": d.get("account"),
        "success": d.get("success"),
    }


def _row(task: str, mode: str, s: dict) -> str:
    return (
        f"| {task} | {mode} | {s['status']} | {s['score']} | "
        f"{s['turns']} | {s['mcp']} | {s['handshake']} | {s['account']} |"
    )


def rewrite_log(rows: list[str]) -> None:
    """Rewrite the AFTER table idempotently: static prefix + the rows so far.

    The prefix is everything up to and including the last markdown separator
    line (the AFTER-table header divider), so re-running the driver after an
    interruption never duplicates rows.
    """
    lines = LOG.read_text().splitlines()
    cut = max(i for i, ln in enumerate(lines) if ln.startswith("|---"))
    prefix = "\n".join(lines[: cut + 1])
    LOG.write_text(prefix + "\n" + "\n".join(rows) + "\n")


def main() -> int:
    LOGDIR.mkdir(parents=True, exist_ok=True)
    if STOP.exists():
        STOP.unlink()
    ai = 0
    rows: list[str] = []
    for task, toml in TASKS:
        for mode in MODES:
            rj = ROOT / f"results/runs/{task}/{mode}/results.json"
            if rj.exists():
                d = json.loads(rj.read_text())
                if d.get("status") is not None:
                    s = summarize(d)
                    prog(f"SKIP {task}/{mode} (post-fix already: status={s['status']})")
                    rows.append(_row(task, mode, s))
                    rewrite_log(rows)
                    continue

            free = free_gb()
            if free < 12:
                prog(f"DISK low ({free:.1f}G) — docker image prune -f")
                subprocess.run(["docker", "image", "prune", "-f"], cwd=ROOT)
                free = free_gb()
                if free < 12:
                    stop(f"low disk {free:.1f}G before {task}/{mode} (after prune)")
                    return 3

            acct = ACCTS[ai % len(ACCTS)]
            ai += 1
            logf = LOGDIR / f"{task}.{mode}.log"
            prog(f"RUN {task}/{mode} acct{acct} free={free:.1f}G")
            t0 = time.monotonic()
            with open(logf, "w") as lf:
                rc = subprocess.run(
                    [
                        "python3", "scripts/orchestration/run_task.py", toml,
                        "--mode", mode, "--account", str(acct), "--agent", AGENT,
                    ],
                    cwd=ROOT, stdout=lf, stderr=subprocess.STDOUT,
                ).returncode
            dt = time.monotonic() - t0

            if not rj.exists():
                stop(f"no results.json for {task}/{mode} (rc={rc}, {dt:.0f}s)")
                return 4
            d = json.loads(rj.read_text())
            s = summarize(d)
            if s["status"] == "INVALID" or not s["turns"]:
                stop(
                    f"{task}/{mode} STILL FAILING post-fix: status={s['status']} "
                    f"turns={s['turns']} mcp={s['mcp']} (rc={rc}) — perm fix did not "
                    f"take for this container; not recording another fake run"
                )
                return 5
            rows.append(_row(task, mode, s))
            rewrite_log(rows)
            prog(
                f"OK {task}/{mode} status={s['status']} turns={s['turns']} "
                f"mcp={s['mcp']} score={s['score']} ({dt:.0f}s)"
            )
    prog("ALL DONE")
    return 0


if __name__ == "__main__":
    sys.exit(main())
