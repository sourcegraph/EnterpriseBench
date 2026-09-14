#!/usr/bin/env bash
# Checkpoint 5: Verify agent traced cross-repo seccomp error propagation chain
set -euo pipefail

REPORT="${WORKSPACE:-/workspace}/moby/INCIDENT_REPORT.md"
if [[ ! -f "$REPORT" ]]; then
  printf '{"score": 0.0, "passed": false, "reason": "INCIDENT_REPORT.md not found"}\n'
  exit 0
fi

FOUND=0
TOTAL=4

# Must name the actual kubelet hop file — "kubelet ... CRI/seccomp" alone is the prompt's own phrasing
if grep -qiE 'security_context\.go|kuberuntime_container_linux' "$REPORT"; then
  FOUND=$((FOUND + 1))
fi

# Must name the actual containerd hop file(s)
if grep -qiE 'container_create_linux|contrib/seccomp|seccomp_default\.go' "$REPORT"; then
  FOUND=$((FOUND + 1))
fi

# Must name the actual runc hop file/function
if grep -qiE 'seccomp_linux\.go|InitSeccomp|buildFilter' "$REPORT"; then
  FOUND=$((FOUND + 1))
fi

# Must name the actual moby hop file
if grep -qiE 'default_linux\.go|profiles/seccomp' "$REPORT"; then
  FOUND=$((FOUND + 1))
fi

if [ "$TOTAL" -gt 0 ]; then
  SCORE=$(awk "BEGIN {printf \"%.2f\", $FOUND/$TOTAL}")
else
  SCORE="0.00"
fi
if [ "$FOUND" -ge 3 ]; then
  PASSED=true
else
  PASSED=false
fi

printf '{"score": %s, "passed": %s, "reason": "Traced %d/%d cross-repo error chain components"}\n' "$SCORE" "$PASSED" "$FOUND" "$TOTAL"
