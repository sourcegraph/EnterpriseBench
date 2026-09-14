#!/usr/bin/env python3
"""Enumerate the llm_curator tasks in the locked N=105 set affected by the
hmcp verifier-soundness bug (un-capped grep recorded when no agent output was
found at the buggy hardcoded artifact paths).

For each task in the locked N=105:
  - locate its task.toml in benchmarks/ and read verification_modes
  - mark llm_curator iff 'llm_curator' in verification_modes AND
    expected_solution.json exists in the task dir (the precondition for the
    Tier-2 cap to apply at all)
  - per arm (baseline, mcp_only), inspect the stored results.json: the fixed
    code stamps cp['judge_score'] on every capped checkpoint, so judge_score
    present == cap was applied (sound), absent == un-capped grep recorded (bug)

Classification per llm_curator task:
  CAPPED              both arms have judge_score      (sound, no re-run needed)
  UNIFORMLY-UNCAPPED  neither arm has judge_score      (unsound, symmetric)
  INCONSISTENT        exactly one arm has judge_score  (poisons the delta)

Read-only. Reproduce: python3 results/analysis/hmcp_affected_enumeration.py
"""
from __future__ import annotations

import json
import re
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
RUNS = REPO / "results" / "runs"
BENCH = REPO / "benchmarks"
SUMMARY = REPO / "results" / "analysis" / "mcp_vs_baseline_clean_summary.md"

# Parse the locked N=105 task list + deltas straight from the headline table.
ROW_RE = re.compile(r"^\|\s*([a-z0-9][a-z0-9-]+)\s*\|\s*([\d.]+)\s*\|\s*([\d.]+)\s*\|\s*([+-]?[\d.]+)\s*\|\s*$")
locked = {}
for line in SUMMARY.read_text().splitlines():
    m = ROW_RE.match(line)
    if m:
        task, base, mcp, delta = m.group(1), float(m.group(2)), float(m.group(3)), float(m.group(4))
        locked[task] = {"baseline": base, "mcp_only": mcp, "delta": delta}

# Build task_id -> benchmarks task dir map, keyed by the `id` field inside each
# task.toml (IDs frequently differ from dir names, e.g. id cal-err-requests-proxy-003
# lives in calibration-003/). Recursive, includes _archived/.
_ID_RE = re.compile(r'^\s*id\s*=\s*"([^"]+)"', re.MULTILINE)
task_dirs = {}
for p in BENCH.rglob("task.toml"):
    m = _ID_RE.search(p.read_text())
    if m:
        task_dirs.setdefault(m.group(1), p.parent)


def verification_modes(task_dir: Path) -> list[str]:
    toml = (task_dir / "task.toml").read_text()
    m = re.search(r"verification_modes\s*=\s*\[([^\]]*)\]", toml)
    if not m:
        return []
    return re.findall(r'"([^"]+)"', m.group(1))


def cap_applied(task: str, arm: str):
    """Return (present, capped, n_cp, n_judge, score) for results/runs/<task>/<arm>/results.json."""
    fp = RUNS / task / arm / "results.json"
    if not fp.is_file():
        return (False, None, 0, 0, None)
    d = json.loads(fp.read_text())
    cps = (d.get("scores") or {}).get("checkpoints") or []
    n_judge = sum(1 for c in cps if "judge_score" in c)
    score = (d.get("scores") or {}).get("task_score")
    return (True, n_judge > 0, len(cps), n_judge, score)


rows = []
for task, deltas in locked.items():
    td = task_dirs.get(task)
    if td is None:
        rows.append({"task": task, "found": False, **deltas})
        continue
    modes = verification_modes(td)
    is_llm = "llm_curator" in modes
    has_exp = (td / "expected_solution.json").exists()
    b_present, b_cap, b_ncp, b_nj, b_score = cap_applied(task, "baseline")
    m_present, m_cap, m_ncp, m_nj, m_score = cap_applied(task, "mcp_only")
    rows.append({
        "task": task, "found": True, "llm_curator": is_llm, "has_expected": has_exp,
        "modes": modes,
        "b_cap": b_cap, "b_nj": b_nj, "b_ncp": b_ncp,
        "m_cap": m_cap, "m_nj": m_nj, "m_ncp": m_ncp,
        **deltas,
    })

# Classify llm_curator tasks (must have expected_solution for cap to be relevant).
affected = [r for r in rows if r.get("found") and r.get("llm_curator") and r.get("has_expected")]


def klass(r):
    b, m = r["b_cap"], r["m_cap"]
    if b and m:
        return "CAPPED"
    if (not b) and (not m):
        return "UNIFORMLY-UNCAPPED"
    return "INCONSISTENT"


for r in affected:
    r["class"] = klass(r)

print(f"Locked N=105 parsed: {len(locked)} tasks")
print(f"llm_curator + expected_solution in N=105: {len(affected)}")
from collections import Counter
c = Counter(r["class"] for r in affected)
print("Class breakdown:", dict(c))
print()
print(f"{'task':45} {'class':20} {'base_cap':8} {'mcp_cap':8} {'delta':>7}")
for r in sorted(affected, key=lambda x: (x["class"], x["task"])):
    print(f"{r['task']:45} {r['class']:20} {str(r['b_cap']):8} {str(r['m_cap']):8} {r['delta']:+7.3f}")

# Also: llm_curator tasks NOT having expected_solution (cap N/A), and non-llm tasks.
llm_no_exp = [r for r in rows if r.get("found") and r.get("llm_curator") and not r.get("has_expected")]
non_llm = [r for r in rows if r.get("found") and not r.get("llm_curator")]
not_found = [r for r in rows if not r.get("found")]
print()
print(f"llm_curator but NO expected_solution.json (cap N/A): {len(llm_no_exp)}")
for r in llm_no_exp:
    print("   ", r["task"])
print(f"non-llm_curator (deterministic-only): {len(non_llm)}")
print(f"task.toml not found in benchmarks/: {len(not_found)}")
for r in not_found:
    print("   ", r["task"])

# Dump machine-readable.
out = REPO / "results" / "analysis" / "hmcp_affected_enumeration.json"
out.write_text(json.dumps({
    "locked_n": len(locked),
    "affected": affected,
    "llm_no_expected": [r["task"] for r in llm_no_exp],
    "non_llm": [r["task"] for r in non_llm],
    "not_found": [r["task"] for r in not_found],
    "class_counts": dict(c),
}, indent=2))
print(f"\nWrote {out.relative_to(REPO)}")
