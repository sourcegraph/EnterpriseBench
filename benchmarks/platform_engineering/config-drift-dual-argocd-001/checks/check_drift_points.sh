#!/usr/bin/env bash
# Checkpoint: agent identified the ArgoCD controller-vs-helm drift points.
# Criteria from expected_solution.json :: drift_points.
set -euo pipefail

REPORT="${WORKSPACE:-/workspace}/DRIFT_REPORT.json"
if [[ ! -f "$REPORT" ]]; then
  printf '{"score": 0.0, "passed": false, "reason": "DRIFT_REPORT.json not found"}\n'
  exit 0
fi

BLOB=$(jq -r '[.. | strings] | join(" ")' "$REPORT" 2>/dev/null || true)
FOUND=0
TOTAL=4

# Controller defaults live in the application-controller command source.
if printf '%s' "$BLOB" | grep -qE 'argocd_application_controller\.go'; then FOUND=$((FOUND + 1)); fi
# Helm override location.
if printf '%s' "$BLOB" | grep -qiE 'values\.yaml|charts/argo-cd'; then FOUND=$((FOUND + 1)); fi
# At least two drift points with concrete config keys.
if jq -e '(.drift_points | length) >= 2' "$REPORT" >/dev/null 2>&1 \
   && printf '%s' "$BLOB" | grep -qiE 'status-processors|operation-processors|appResyncPeriod|resourceTrackingMethod|resource.?tracking|sync.*timeout'; then
  FOUND=$((FOUND + 1))
fi
# Additional controller source files.
if printf '%s' "$BLOB" | grep -qE 'controller/appcontroller\.go|appcontroller\.go|util/settings/settings\.go|settings\.go'; then FOUND=$((FOUND + 1)); fi

SCORE=$(awk "BEGIN {printf \"%.2f\", $FOUND/$TOTAL}")
if [ "$FOUND" -ge 2 ]; then PASSED=true; else PASSED=false; fi
printf '{"score": %s, "passed": %s, "reason": "Found %d/%d drift-point criteria"}\n' "$SCORE" "$PASSED" "$FOUND" "$TOTAL"
