#!/usr/bin/env bash
# Checkpoint: Verify agent identified the actual drift point (set precision/recall).
set -euo pipefail

export WORKSPACE="${WORKSPACE:-/workspace}"
export REPORT="$WORKSPACE/charts/DRIFT_REPORT.json"

if [[ ! -f "$REPORT" ]]; then
  printf '{"score": 0.0, "passed": false, "reason": "DRIFT_REPORT.json not found"}\n'
  exit 0
fi

FOUND=0
TOTAL=2

# Repo-specific artifacts only — generic prompt terms (sync timeouts, retry
# settings, resource tracking, health checks) are given in the prompt and must
# not earn credit by themselves.

# 1. Anchored in argo-cd: the actual constant/function/file backing the
#    controller's built-in instance-label-key default.
if python3 -c "
import json, os, re
try:
    report = json.load(open(os.environ['REPORT']))
except Exception:
    exit(1)
blob = json.dumps(report)
exit(0 if re.search(r'LabelKeyAppInstance|GetAppInstanceLabelKey|common/common\.go|util/settings/settings\.go', blob) else 1)
" 2>/dev/null; then
  FOUND=$((FOUND + 1))
fi

# 2. Anchored in argo-helm: the actual overridden value the chart sets in
#    argocd-cm, which diverges from the argo-cd built-in default.
if python3 -c "
import json, os, re
try:
    report = json.load(open(os.environ['REPORT']))
except Exception:
    exit(1)
blob = json.dumps(report)
exit(0 if re.search(r'argocd\.argoproj\.io/instance|application\.instanceLabelKey', blob) else 1)
" 2>/dev/null; then
  FOUND=$((FOUND + 1))
fi

SCORE=$(awk "BEGIN {printf \"%.2f\", $FOUND/$TOTAL}")
if [ "$FOUND" -ge 2 ]; then PASSED=true; else PASSED=false; fi

printf '{"score": %s, "passed": %s, "reason": "Found %d/%d drift point anchors"}\n' "$SCORE" "$PASSED" "$FOUND" "$TOTAL"
