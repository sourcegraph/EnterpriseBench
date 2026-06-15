#!/usr/bin/env bash
# check_feature_flags.sh — Verify feature-flag identification.
# NOTE: this dual task carries no feature_flags in ground truth; when absent this
# checkpoint is a default-pass. That default-pass-on-absent-GT behaviour is the known
# garbage_credit / FP-inflation item deferred to the P2 verifier-hardening pass
# (EnterpriseBench-y50 recommendation #2) — only the report path is repaired here.
# Env: WORKSPACE, TASK_DIR, TASK_ID
set -euo pipefail

REPORT="${WORKSPACE:-/workspace}/dead_code_report.json"
GT="${TASK_DIR}/ground_truth.json"

if [[ ! -f "$REPORT" ]]; then
    echo '{"score": 0.0, "detail": "No dead_code_report.json found"}'
    exit 0
fi

python3 - "$REPORT" "$GT" <<'PYEOF'
import json, sys

try:
    claimed = json.load(open(sys.argv[1]))
except (json.JSONDecodeError, OSError):
    print(json.dumps({"score": 0.0, "detail": "Invalid JSON in dead_code_report.json"})); sys.exit(0)

gt = json.load(open(sys.argv[2]))
gt_flag_names = {f["flag"].lower() for f in gt.get("feature_flags", [])}

if not gt_flag_names:
    print(json.dumps({"score": 1.0, "detail": "No feature flags in ground truth"})); sys.exit(0)

found = set()
for item in (claimed if isinstance(claimed, list) else []):
    combined = json.dumps(item).lower()
    for flag in gt_flag_names:
        if flag in combined:
            found.add(flag)

recall = len(found) / len(gt_flag_names)
print(json.dumps({"score": round(recall, 4), "detail": f"flags_found={len(found)}/{len(gt_flag_names)}"}))
PYEOF
