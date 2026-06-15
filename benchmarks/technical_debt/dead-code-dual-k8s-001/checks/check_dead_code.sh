#!/usr/bin/env bash
# check_dead_code.sh — verify the agent identified the removed/deprecated Kubernetes API
# groups whose client-go code is now dead. Scores coverage of the curated dead API surface
# (ground_truth.dead_code, matched on the api_group's group + version tokens) plus structural
# quality of the cross-repo entries. Schema follows the task instruction's output shape.
# Env: WORKSPACE, TASK_DIR, TASK_ID
set -euo pipefail

REPORT="${WORKSPACE:-/workspace}/dead_code_report.json"
GT="${TASK_DIR}/ground_truth.json"

if [[ ! -f "$REPORT" ]]; then
    echo '{"score": 0.0, "detail": "No dead_code_report.json found"}'
    exit 0
fi

python3 - "$REPORT" "$GT" <<'PYEOF'
import json, os, sys, re

try:
    claimed = json.load(open(sys.argv[1]))
except (json.JSONDecodeError, OSError):
    print(json.dumps({"score": 0.0, "detail": "Invalid JSON in dead_code_report.json"})); sys.exit(0)

if not isinstance(claimed, list) or not claimed:
    print(json.dumps({"score": 0.0, "detail": "Report is empty or not a list"})); sys.exit(0)

gt = json.load(open(sys.argv[2]))
gt_dead = gt.get("dead_code", [])

def blob(e):
    return json.dumps(e).lower()

# Recall: each curated dead API group must be covered by a claimed entry. Match on the
# distinctive group short-name plus its version (e.g. "flowcontrol" + "v1beta2"), so an
# answer that writes "flowcontrol/v1beta2" or the full group string both count.
covered = 0
for g in gt_dead:
    group = str(g.get("api_group", "")).lower()
    short = group.split(".")[0] if group else ""
    m = re.search(r"v\d+(alpha|beta)?\d*", group)
    ver = m.group(0) if m else ""
    if short and ver and any(short in blob(c) and ver in blob(c) for c in claimed):
        covered += 1
recall = covered / len(gt_dead) if gt_dead else 0.0

# Structure: well-formed cross-repo entries per the instruction schema.
valid = sum(
    1 for c in claimed
    if isinstance(c, dict) and c.get("api_group") and c.get("client_go_files")
)
struct = valid / len(claimed)

score = round(0.6 * recall + 0.4 * struct, 4)
detail = f"recall={recall:.2f} ({covered}/{len(gt_dead)}) struct={struct:.2f} ({valid}/{len(claimed)})"
print(json.dumps({"score": score, "detail": detail}))
PYEOF
