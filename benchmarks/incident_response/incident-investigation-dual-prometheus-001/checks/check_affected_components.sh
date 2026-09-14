#!/usr/bin/env bash
# Checkpoint 3: Verify agent identified affected alerting components
set -euo pipefail

export REPORT="${WORKSPACE:-/workspace}/alertmanager/INCIDENT_REPORT.md"
if [[ ! -f "$REPORT" ]]; then
  printf '{"score": 0.0, "passed": false, "reason": "INCIDENT_REPORT.md not found"}\n'
  exit 0
fi

FOUND=0
TOTAL=2

# Must mention recording rule evaluation as affected
if grep -qiE 'recording.*rule|rules.*manager|rule.*evaluat' "$REPORT"; then
  FOUND=$((FOUND + 1))
fi

# Must mention the actual Alertmanager inhibition/dispatch source files, not
# just the generic "dispatch" word already used in the prompt's own symptoms.
if grep -qiE 'inhibit/inhibit(or)?\.go|dispatch/dispatch\.go|type[[:space:]]+Inhibitor|func.*Mutes\(|hasEqual\(' "$REPORT"; then
  FOUND=$((FOUND + 1))
fi

if [ "$TOTAL" -gt 0 ]; then
  SCORE=$(awk "BEGIN {printf \"%.2f\", $FOUND/$TOTAL}")
else
  SCORE="0.00"
fi
if [ "$FOUND" -ge 1 ]; then
  PASSED=true
else
  PASSED=false
fi

printf '{"score": %s, "passed": %s, "reason": "Identified %d/%d affected components"}\n' "$SCORE" "$PASSED" "$FOUND" "$TOTAL"
