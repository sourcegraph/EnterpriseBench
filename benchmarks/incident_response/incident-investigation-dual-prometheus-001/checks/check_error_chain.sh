#!/usr/bin/env bash
# Checkpoint 2: Verify agent traced the inhibition chain
set -euo pipefail

export REPORT="${WORKSPACE:-/workspace}/alertmanager/INCIDENT_REPORT.md"
if [[ ! -f "$REPORT" ]]; then
  printf '{"score": 0.0, "passed": false, "reason": "INCIDENT_REPORT.md not found"}\n'
  exit 0
fi

FOUND=0
TOTAL=4

# Must mention recording rule as starting point
if grep -qiE 'recording.*rule|node.*ready.*condition' "$REPORT"; then
  FOUND=$((FOUND + 1))
fi

# Must mention perpetual/always-firing source alert
if grep -qiE 'perpetual|always.*fir|permanent.*fir|NodeNotReady' "$REPORT"; then
  FOUND=$((FOUND + 1))
fi

# Discriminating hop: must cite the actual Alertmanager inhibition matcher
# code path, not just the generic "inhibit/suppress" vocabulary already given
# in the prompt's own symptom description.
if grep -qiE 'inhibit/inhibit(or)?\.go|type[[:space:]]+Inhibitor|func.*Mutes\(|hasEqual\(|InhibitRule' "$REPORT"; then
  FOUND=$((FOUND + 1))
fi

# Must mention target alert being suppressed
if grep -qiE 'DiskUsage|target.*alert.*suppress|critical.*suppress|never.*dispatch' "$REPORT"; then
  FOUND=$((FOUND + 1))
fi

if [ "$TOTAL" -gt 0 ]; then
  SCORE=$(awk "BEGIN {printf \"%.2f\", $FOUND/$TOTAL}")
else
  SCORE="0.00"
fi
if [ "$FOUND" -ge 2 ]; then
  PASSED=true
else
  PASSED=false
fi

printf '{"score": %s, "passed": %s, "reason": "Traced %d/%d inhibition chain steps"}\n' "$SCORE" "$PASSED" "$FOUND" "$TOTAL"
