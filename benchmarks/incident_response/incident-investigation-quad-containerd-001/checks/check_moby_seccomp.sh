#!/usr/bin/env bash
# Checkpoint 4: Verify agent identified moby default seccomp profile and newer syscalls
set -euo pipefail

REPORT="${WORKSPACE:-/workspace}/moby/INCIDENT_REPORT.md"
if [[ ! -f "$REPORT" ]]; then
  printf '{"score": 0.0, "passed": false, "reason": "INCIDENT_REPORT.md not found"}\n'
  exit 0
fi

FOUND=0
TOTAL=3

# Must name the actual moby seccomp profile file, not just "moby seccomp profile" (already in prompt)
if grep -qiE 'profiles/seccomp/default_linux\.go|profiles/seccomp/seccomp\.go' "$REPORT"; then
  FOUND=$((FOUND + 1))
fi

# Must name the actual profile-generation symbol, not the generic "default profile" / "RuntimeDefault"
# phrasing already given in the prompt
if grep -qiE 'DefaultProfile\(|func DefaultProfile|profiles/seccomp/default_linux' "$REPORT"; then
  FOUND=$((FOUND + 1))
fi

# Must name a specific syscall the default profile whitelists beyond the one already named in the
# alert log (clone3) — forces the agent to actually read the profile rather than echo the alert
if grep -qiE 'close_range|openat2|faccessat2|pidfd_open' "$REPORT"; then
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

printf '{"score": %s, "passed": %s, "reason": "Identified %d/%d moby default seccomp profile elements"}\n' "$SCORE" "$PASSED" "$FOUND" "$TOTAL"
