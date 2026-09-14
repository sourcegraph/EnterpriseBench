#!/usr/bin/env bash
# Checkpoint 2: Verify agent traced containerd CRI plugin seccomp handling
set -euo pipefail

REPORT="${WORKSPACE:-/workspace}/moby/INCIDENT_REPORT.md"
if [[ ! -f "$REPORT" ]]; then
  printf '{"score": 0.0, "passed": false, "reason": "INCIDENT_REPORT.md not found"}\n'
  exit 0
fi

FOUND=0
TOTAL=3

# Must name the actual containerd CRI seccomp mapping file (not just "containerd CRI plugin")
if grep -qiE 'container_create_linux\.go|pkg/cri/server/container_create_linux|cri.*server.*container_create' "$REPORT"; then
  FOUND=$((FOUND + 1))
fi

# Must name the OCI runtime-spec seccomp field/struct actually populated (LinuxSeccomp / linux.seccomp),
# not just the generic phrase "OCI runtime spec" already given in the prompt
if grep -qiE 'LinuxSeccomp|linux\.seccomp|specs\.Linux\.Seccomp' "$REPORT"; then
  FOUND=$((FOUND + 1))
fi

# Must name the containerd seccomp helper file, not just say "containerd processes seccomp"
if grep -qiE 'seccomp_default\.go|contrib/seccomp' "$REPORT"; then
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

printf '{"score": %s, "passed": %s, "reason": "Identified %d/%d containerd CRI seccomp mapping elements"}\n' "$SCORE" "$PASSED" "$FOUND" "$TOTAL"
