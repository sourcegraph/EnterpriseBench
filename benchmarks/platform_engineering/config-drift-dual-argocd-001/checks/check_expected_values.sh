#!/usr/bin/env bash
# Checkpoint: Verify agent determined the correct paired values (not swapped/
# hallucinated) and traced the actual override chain.
set -euo pipefail

export WORKSPACE="${WORKSPACE:-/workspace}"
export REPORT="$WORKSPACE/charts/DRIFT_REPORT.json"

if [[ ! -f "$REPORT" ]]; then
  printf '{"score": 0.0, "passed": false, "reason": "DRIFT_REPORT.json not found"}\n'
  exit 0
fi

FOUND=0
TOTAL=2

# 1. Within the SAME drift point, argocd_default must be the real controller
#    default (app.kubernetes.io/instance) and helm_override must be the real
#    chart override (argocd.argoproj.io/instance) — catches value swaps and
#    generic/hallucinated pairs that don't match the actual repos.
if python3 -c "
import json, os
try:
    report = json.load(open(os.environ['REPORT']))
except Exception:
    exit(1)
points = report.get('drift_points', [])
for p in points:
    if not isinstance(p, dict):
        continue
    default = str(p.get('argocd_default', '')).lower()
    override = str(p.get('helm_override', '')).lower()
    if 'app.kubernetes.io/instance' in default and 'argocd.argoproj.io/instance' in override:
        exit(0)
exit(1)
" 2>/dev/null; then
  FOUND=$((FOUND + 1))
fi

# 2. Override chain traces the actual mechanism: argocd-cm ConfigMap read
#    through the settings manager fallback, not just a generic sentence.
if python3 -c "
import json, os, re
try:
    report = json.load(open(os.environ['REPORT']))
except Exception:
    exit(1)
blob = json.dumps(report)
exit(0 if re.search(r'argocd-cm|GetAppInstanceLabelKey|settings\.go|SettingsManager', blob) else 1)
" 2>/dev/null; then
  FOUND=$((FOUND + 1))
fi

SCORE=$(awk "BEGIN {printf \"%.2f\", $FOUND/$TOTAL}")
if [ "$FOUND" -ge 1 ]; then PASSED=true; else PASSED=false; fi

printf '{"score": %s, "passed": %s, "reason": "Correct expected values for %d/%d checks"}\n' "$SCORE" "$PASSED" "$FOUND" "$TOTAL"
