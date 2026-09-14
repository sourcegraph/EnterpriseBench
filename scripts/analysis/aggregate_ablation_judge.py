#!/usr/bin/env python3
"""Aggregate ablation scores with grep, judge, and min(grep, judge).

Reads:
- Fresh ablation results from results/runs/<task>/ablate-*/rep*/results.json
  (these have grep_score and judge_score per checkpoint after Tier 2 was applied)
- Optional sidecar judge scores from /tmp/rescore_*.log (for tasks rescored
  from existing agent logs without re-running)

Writes:
- results/phase1_pilot/ablation_judge_summary.csv — per-checkpoint, per-rep
- results/phase1_pilot/ablation_judge_summary.md — human-readable table

Usage:
    python scripts/analysis/aggregate_ablation_judge.py
"""
from __future__ import annotations

import csv
import json
import re
from pathlib import Path
from typing import Iterable

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
RESULTS_DIR = REPO_ROOT / "results" / "runs"
OUT_DIR = REPO_ROOT / "results" / "phase1_pilot"

ABLATIONS: tuple[tuple[str, str, tuple[str, ...]], ...] = (
    (
        "incident-inv-docker-shutdown-004",
        "benchmarks/incident_response/incident-investigation-004",
        ("ablate-moby", "ablate-containerd"),
    ),
    (
        "support-map-grafana-alerts-004",
        "benchmarks/customer_escalation/support-mapping-004",
        ("ablate-grafana", "ablate-alertmanager"),
    ),
)

# Map verifier-emitted (short) checkpoint name -> canonical (task.toml) name
INCIDENT_NAME_MAP = {
    "root_cause": "root_cause_identification",
    "error_chain": "error_chain_trace",
    "remediation": "remediation_proposal",
    "affected_services": "affected_services",
}


def load_rescore_log(path: Path) -> dict[tuple[str, int, str], float]:
    """Parse /tmp/rescore_support.log into {(variant, rep, checkpoint): judge_score}.

    Format example::

        === ablate-grafana ===
          rep1:
            grep_total=3.2  judge_total=2.5
            code_paths                      grep=0.53  judge=0.50
            ...
    """
    if not path.exists():
        return {}

    out: dict[tuple[str, int, str], float] = {}
    variant: str | None = None
    rep: int | None = None

    variant_re = re.compile(r"^=== (ablate-\S+) ===$")
    rep_re = re.compile(r"^\s*rep(\d+):\s*$")
    cp_re = re.compile(
        r"^\s*(\S+)\s+grep=([0-9.]+)\s+judge=([-0-9.]+)"
    )

    for line in path.read_text().splitlines():
        m = variant_re.match(line)
        if m:
            variant = m.group(1)
            rep = None
            continue
        m = rep_re.match(line)
        if m:
            rep = int(m.group(1))
            continue
        m = cp_re.match(line)
        if m and variant and rep:
            cp_name = m.group(1)
            judge = float(m.group(3))
            out[(variant, rep, cp_name)] = judge
    return out


def collect_rows(
    rescore_log: dict[tuple[str, int, str], float],
) -> list[dict[str, object]]:
    rows: list[dict[str, object]] = []

    for task_id, _task_dir, variants in ABLATIONS:
        for variant in variants:
            for rep in (1, 2, 3):
                rep_dir = RESULTS_DIR / task_id / variant / f"rep{rep}"
                results_file = rep_dir / "results.json"
                if not results_file.is_file():
                    continue

                data = json.loads(results_file.read_text())
                checkpoints = data.get("scores", {}).get("checkpoints", [])
                for cp in checkpoints:
                    short_name = cp.get("name", "")
                    canonical = INCIDENT_NAME_MAP.get(short_name, short_name)
                    grep_score = cp.get("grep_score")
                    if grep_score is None:
                        grep_score = cp.get("score")
                    judge_score = cp.get("judge_score")

                    # Fall back to rescore log for tasks that didn't re-run
                    if judge_score is None:
                        judge_score = rescore_log.get((variant, rep, short_name))

                    if judge_score is None:
                        # No judge data — emit grep only
                        final_score = grep_score
                    else:
                        final_score = min(grep_score, judge_score)

                    rows.append(
                        {
                            "task_id": task_id,
                            "variant": variant,
                            "rep_index": rep,
                            "checkpoint": canonical,
                            "weight": cp.get("weight", 1.0),
                            "grep_score": round(grep_score, 4)
                            if grep_score is not None
                            else "",
                            "judge_score": round(judge_score, 4)
                            if judge_score is not None
                            else "",
                            "min_score": round(final_score, 4)
                            if final_score is not None
                            else "",
                            "passed": cp.get("passed"),
                        }
                    )
    return rows


def write_csv(rows: list[dict[str, object]], path: Path) -> None:
    if not rows:
        path.write_text("task_id,variant,rep_index,checkpoint,grep_score,judge_score,min_score\n")
        return
    fieldnames = list(rows[0].keys())
    with path.open("w", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)


def summarise(rows: Iterable[dict[str, object]]) -> str:
    """Produce a markdown summary highlighting removed-repo checkpoints."""
    by_variant: dict[tuple[str, str, str], list[float]] = {}
    for r in rows:
        if r["min_score"] == "":
            continue
        key = (str(r["task_id"]), str(r["variant"]), str(r["checkpoint"]))
        by_variant.setdefault(key, []).append(float(r["min_score"]))

    lines = [
        "# Ablation Judge Summary",
        "",
        "Removed-repo checkpoints should score <= 0.30 per PRD CRNT threshold.",
        "",
        "| task_id | variant | checkpoint | n | mean_min | max_min | <=0.30? |",
        "| --- | --- | --- | ---: | ---: | ---: | --- |",
    ]
    for (task_id, variant, cp), scores in sorted(by_variant.items()):
        mean = sum(scores) / len(scores)
        mx = max(scores)
        marker = "yes" if mx <= 0.30 else "no"
        lines.append(
            f"| {task_id} | {variant} | {cp} | {len(scores)} | "
            f"{mean:.2f} | {mx:.2f} | {marker} |"
        )
    return "\n".join(lines) + "\n"


def main() -> int:
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    rescore_log = load_rescore_log(Path("/tmp/rescore_support.log"))
    rows = collect_rows(rescore_log)

    csv_path = OUT_DIR / "ablation_judge_summary.csv"
    md_path = OUT_DIR / "ablation_judge_summary.md"
    write_csv(rows, csv_path)
    md_path.write_text(summarise(rows))

    print(f"Wrote {len(rows)} rows to {csv_path}")
    print(f"Wrote summary to {md_path}")
    print()
    print(md_path.read_text())
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
