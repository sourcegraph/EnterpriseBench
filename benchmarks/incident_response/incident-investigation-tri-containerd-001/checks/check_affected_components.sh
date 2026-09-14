#!/usr/bin/env bash
# Checkpoint 3: Verify agent identified affected components in all three repos
set -euo pipefail

export REPORT="${WORKSPACE:-/workspace}/moby/INCIDENT_REPORT.md"
if [[ ! -f "$REPORT" ]]; then
  printf '{"score": 0.0, "passed": false, "reason": "INCIDENT_REPORT.md not found"}\n'
  exit 0
fi

FOUND=0
TOTAL=3

# Repo-specific artifacts only — bare repo names (moby, containerd, runc) are
# already given in the prompt's Environment section and must not earn credit.

# Must cite the moby daemon start entry point
if grep -qiE 'daemon/start\.go|daemon/oci_linux\.go' "$REPORT"; then
  FOUND=$((FOUND + 1))
fi

# Must cite the containerd shim process file
if grep -qiE 'pkg/process/init\.go|containerd.*shim.*process' "$REPORT"; then
  FOUND=$((FOUND + 1))
fi

# Must cite the runc process execution file
if grep -qiE 'libcontainer/process_linux\.go|process_linux\.go' "$REPORT"; then
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

printf '{"score": %s, "passed": %s, "reason": "Identified %d/%d affected runtime components"}\n' "$SCORE" "$PASSED" "$FOUND" "$TOTAL"
