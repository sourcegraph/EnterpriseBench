#!/usr/bin/env bash
# Checkpoint 4: Verify agent proposed a concrete fix in the framework driver
set -euo pipefail

REPORT="${WORKSPACE:-/workspace}/INCIDENT_REPORT.md"
if [[ ! -f "$REPORT" ]]; then
  printf '{"score": 0.0, "passed": false, "reason": "INCIDENT_REPORT.md not found"}\n'
  exit 0
fi

FOUND=0
TOTAL=2

# Repo-specific artifacts only — a generic "fix the version mismatch"
# sentence must not earn credit; require the actual mechanism/symbol.

# Proposes reading version from module, exposing option, or defaulting to v1
if grep -qiE 'RegoV1|DriverOpt|driver.*option|SetRegoVersion.*module|read.*module.*version|module.*declared.*version' "$REPORT"; then
  FOUND=$((FOUND + 1))
fi

# Indicates fix is in framework driver (specific path), not OPA
if grep -qiE 'drivers/rego/driver\.go|rego/driver\.go|driver\.go' "$REPORT"; then
  FOUND=$((FOUND + 1))
fi

SCORE=$(awk "BEGIN {printf \"%.2f\", $FOUND/$TOTAL}")
if [ "$FOUND" -ge 1 ]; then
  PASSED=true
else
  PASSED=false
fi

printf '{"score": %s, "passed": %s, "reason": "Remediation quality: %d/%d key elements"}\n' "$SCORE" "$PASSED" "$FOUND" "$TOTAL"
