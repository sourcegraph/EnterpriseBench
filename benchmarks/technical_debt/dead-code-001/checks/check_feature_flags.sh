#!/usr/bin/env bash
# check_feature_flags.sh — Verify feature flag identification.
# Checks that agent identified permanently-on/off flags.
# Env: WORKSPACE, TASK_DIR, TASK_ID
set -euo pipefail

REPORT="${WORKSPACE}/agent_output/dead_code_report.json"
GT_DIR="${TASK_DIR}/ground_truth.json"

if [[ ! -f "$REPORT" ]]; then
    echo '{"score": 0.0, "detail": "No dead_code_report.json found"}'
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

gt_flags = gt.get("feature_flags", [])
gt_flag_names = {f["flag"].lower() for f in gt_flags}

if not gt_flag_names:
    print(json.dumps({"score": 1.0, "detail": "No feature flags in ground truth"}))
    sys.exit(0)

# Check if any claimed items mention the feature flags in their evidence
found_flags = set()
for item in claimed:
    evidence = item.get("evidence", "").lower()
    symbol = item.get("symbol", "").lower()
    file_path = item.get("file", "").lower()
    combined = f"{evidence} {symbol} {file_path}"
    for flag in gt_flag_names:
        if flag in combined:
            found_flags.add(flag)

recall = len(found_flags) / len(gt_flag_names) if gt_flag_names else 0.0
score = recall

detail = f"flags_found={len(found_flags)}/{len(gt_flag_names)} ({', '.join(sorted(found_flags)) or 'none'})"
print(json.dumps({"score": round(score, 4), "detail": detail}))
PYEOF
