#!/usr/bin/env bash
# check_evidence.sh — Validate that each dead code claim has evidence.
# Env: WORKSPACE, TASK_DIR, TASK_ID
set -euo pipefail

REPORT="${WORKSPACE:-/workspace}/dead_code_report.json"

if [[ ! -f "$REPORT" ]]; then
    echo '{"score": 0.0, "detail": "No dead_code_report.json found"}'
    exit 0
fi

python3 - "$REPORT" <<'PYEOF'
import json, sys

try:
    claimed = json.load(open(sys.argv[1]))
except (json.JSONDecodeError, OSError):
    print(json.dumps({"score": 0.0, "detail": "Invalid JSON in dead_code_report.json"})); sys.exit(0)

if not isinstance(claimed, list) or not claimed:
    print(json.dumps({"score": 0.0, "detail": "Empty report"})); sys.exit(0)

QUALITY_KEYWORDS = [
    "never called", "no callers", "zero callers", "no references",
    "zero references", "unused", "not imported", "no importers",
    "dead", "unreachable", "removed", "deprecated", "permanently",
    "always", "never", "no longer",
]

has_evidence = quality = 0
for item in claimed:
    evidence = str(item.get("evidence", "")).strip()
    if evidence:
        has_evidence += 1
        if any(kw in evidence.lower() for kw in QUALITY_KEYWORDS):
            quality += 1

total = len(claimed)
score = round(0.6 * (has_evidence / total) + 0.4 * (quality / total), 4)
print(json.dumps({"score": score, "detail": f"entries={total} has_evidence={has_evidence} quality={quality}"}))
PYEOF
