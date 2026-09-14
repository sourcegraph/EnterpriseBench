#!/usr/bin/env bash
# Checkpoint 1: Verify agent identified the framework driver pin and OPA-side enforcement
set -euo pipefail

REPORT="${WORKSPACE:-/workspace}/INCIDENT_REPORT.md"
if [[ ! -f "$REPORT" ]]; then
  printf '{"score": 0.0, "passed": false, "reason": "INCIDENT_REPORT.md not found"}\n'
  exit 0
fi

FOUND=0
TOTAL=3

# Names the driver file as the pin site
if grep -qiE 'drivers/rego/driver\.go|rego/driver\.go|driver\.go.*eval' "$REPORT"; then
  FOUND=$((FOUND + 1))
fi

# Names the specific RegoV0 pin or SetRegoVersion call
if grep -qiE 'SetRegoVersion|RegoV0|Rego.*V0|rego\.v0|RegoVersion.*v0' "$REPORT"; then
  FOUND=$((FOUND + 1))
fi

# References OPA-side parser or topdown
if grep -qiE 'topdown/eval\.go|ast/parser\.go|opa.*topdown|opa.*parser|parser\.go|topdown\.eval' "$REPORT"; then
  FOUND=$((FOUND + 1))
fi

SCORE=$(awk "BEGIN {printf \"%.2f\", $FOUND/$TOTAL}")
if [ "$FOUND" -ge 2 ]; then
  PASSED=true
else
  PASSED=false
fi

printf '{"score": %s, "passed": %s, "reason": "Identified %d/%d root cause elements"}\n' "$SCORE" "$PASSED" "$FOUND" "$TOTAL"
