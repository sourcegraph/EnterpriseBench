#!/usr/bin/env bash
# Checkpoint: agent determined the correct expected (controller-default) values.
# Criteria from expected_solution.json :: expected_values.
set -euo pipefail

REPORT="${WORKSPACE:-/workspace}/DRIFT_REPORT.json"
if [[ ! -f "$REPORT" ]]; then
  printf '{"score": 0.0, "passed": false, "reason": "DRIFT_REPORT.json not found"}\n'
  exit 0
fi

BLOB=$(jq -r '[.. | strings] | join(" ")' "$REPORT" 2>/dev/null || true)
FOUND=0
TOTAL=3

# Go-side flag defaults with their source file (status-processors=20 / operation-processors=10 / appResyncPeriod=180s).
if printf '%s' "$BLOB" | grep -qE 'argocd_application_controller\.go' \
   && printf '%s' "$BLOB" | grep -qiE 'status-processors|operation-processors|appResyncPeriod' \
   && printf '%s' "$BLOB" | grep -qE '\b(20|10|180)\b'; then
  FOUND=$((FOUND + 1))
fi
# resourceTrackingMethod='label' default in util/settings/settings.go.
if printf '%s' "$BLOB" | grep -qiE 'resourceTrackingMethod|resource.?tracking' \
   && printf '%s' "$BLOB" | grep -qiE 'label'; then
  FOUND=$((FOUND + 1))
fi
# Override chain: Go default -> helm-rendered deployment args -> running controller -> reconcile loop.
if jq -e '.drift_points | any((.override_chain // []) | length > 0)' "$REPORT" >/dev/null 2>&1 \
   || printf '%s' "$BLOB" | grep -qiE 'reconcile|override chain|deployment args|rendered'; then
  FOUND=$((FOUND + 1))
fi

SCORE=$(awk "BEGIN {printf \"%.2f\", $FOUND/$TOTAL}")
if [ "$FOUND" -ge 2 ]; then PASSED=true; else PASSED=false; fi
printf '{"score": %s, "passed": %s, "reason": "Correct expected values: %d/%d criteria"}\n' "$SCORE" "$PASSED" "$FOUND" "$TOTAL"
