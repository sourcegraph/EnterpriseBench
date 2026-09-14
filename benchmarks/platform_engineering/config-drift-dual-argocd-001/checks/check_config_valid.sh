#!/usr/bin/env bash
# Checkpoint: Agent produces a valid, cross-repo-grounded configuration analysis.
set -euo pipefail

export WORKSPACE="${WORKSPACE:-/workspace}"
export REPORT="$WORKSPACE/charts/DRIFT_REPORT.json"

if [[ ! -f "$REPORT" ]]; then
  printf '{"score": 0.0, "passed": false, "reason": "DRIFT_REPORT.json not found"}\n'
  exit 0
fi

FOUND=0
TOTAL=2

# 1. Report is valid JSON with a non-empty drift_points array where at least one
#    entry has all the required fields populated (not blank/placeholder).
if python3 -c "
import json, os
try:
    report = json.load(open(os.environ['REPORT']))
except Exception:
    exit(1)
points = report.get('drift_points', [])
if not isinstance(points, list) or not points:
    exit(1)
required = ['config_key', 'argocd_default', 'argocd_source_file', 'helm_override', 'helm_source_path', 'impact']
for p in points:
    if not isinstance(p, dict):
        continue
    if all(str(p.get(k, '')).strip() for k in required):
        exit(0)
exit(1)
" 2>/dev/null; then
  FOUND=$((FOUND + 1))
fi

# 2. Report cites a concrete source file from BOTH repos (not just generic prose) —
#    an argo-cd Go file where the controller default actually lives, and an
#    argo-helm values.yaml / configs path where the chart sets it.
if python3 -c "
import json, os, re
try:
    report = json.load(open(os.environ['REPORT']))
except Exception:
    exit(1)
blob = json.dumps(report)
argocd_file = re.search(r'common/common\.go|util/settings/settings\.go|controller/appcontroller\.go|cmd/argocd-application-controller', blob)
helm_path = re.search(r'charts/argo-cd/values\.yaml|configs\.cm|configs/params', blob)
exit(0 if (argocd_file and helm_path) else 1)
" 2>/dev/null; then
  FOUND=$((FOUND + 1))
fi

SCORE=$(awk "BEGIN {printf \"%.2f\", $FOUND/$TOTAL}")
if [ "$FOUND" -ge 2 ]; then PASSED=true; else PASSED=false; fi

printf '{"score": %s, "passed": %s, "reason": "Valid, repo-grounded analysis: %d/%d"}\n' "$SCORE" "$PASSED" "$FOUND" "$TOTAL"
