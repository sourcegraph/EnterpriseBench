#!/usr/bin/env bash
# check_dead_code.sh — Verify dead code identification using precision-weighted scoring.
# Env: WORKSPACE, TASK_DIR, TASK_ID
set -euo pipefail

REPORT="${WORKSPACE}/agent_output/dead_code_report.json"
GT_DIR="${TASK_DIR}/ground_truth.json"

if [[ ! -f "$REPORT" ]]; then
    echo '{"score": 0.0, "detail": "No dead_code_report.json found"}'
    exit 0
fi

if [[ ! -f "$GT_DIR" ]]; then
    echo '{"score": 0.0, "detail": "Ground truth not found"}'
    exit 0
fi

python3 - "$REPORT" "$GT_DIR" <<'PYEOF'
import json
import sys

report_path = sys.argv[1]
gt_path = sys.argv[2]

with open(report_path) as f:
    claimed = json.load(f)

with open(gt_path) as f:
    gt = json.load(f)

gt_dead = gt.get("dead_code", [])
gt_live = gt.get("live_code", [])

def normalize(items):
    return {(e["file"], e["symbol"]) for e in items}

claimed_set = normalize(claimed)
dead_set = normalize(gt_dead)
live_set = normalize(gt_live)

tp = claimed_set & dead_set
fp = claimed_set & live_set
fn = dead_set - claimed_set

tp_count = len(tp)
fp_count = len(fp)
fn_count = len(fn)

beta = 0.5
beta_sq = beta ** 2

precision = tp_count / (tp_count + fp_count) if (tp_count + fp_count) > 0 else 0.0
recall = tp_count / (tp_count + fn_count) if (tp_count + fn_count) > 0 else 0.0

if precision + recall > 0:
    f_score = (1 + beta_sq) * (precision * recall) / (beta_sq * precision + recall)
else:
    f_score = 0.0

if precision < 0.9:
    score = f_score * 0.7
elif recall < 0.6:
    score = f_score * 0.85
else:
    score = f_score

detail = (
    f"precision={precision:.3f} recall={recall:.3f} f0.5={f_score:.3f} "
    f"TP={tp_count} FP={fp_count} FN={fn_count}"
)

print(json.dumps({"score": round(score, 4), "detail": detail}))
PYEOF
