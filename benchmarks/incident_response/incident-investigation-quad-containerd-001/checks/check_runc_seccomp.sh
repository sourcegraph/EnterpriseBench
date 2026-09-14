#!/usr/bin/env bash
# Checkpoint 3: Verify agent identified runc libcontainer seccomp BPF filter generation
set -euo pipefail

REPORT="${WORKSPACE:-/workspace}/moby/INCIDENT_REPORT.md"
if [[ ! -f "$REPORT" ]]; then
  printf '{"score": 0.0, "passed": false, "reason": "INCIDENT_REPORT.md not found"}\n'
  exit 0
fi

FOUND=0
TOTAL=3

# Must name the actual runc file/function, not the generic "runc BPF filter" / "libcontainer seccomp"
# phrasing already given in the prompt
if grep -qiE 'seccomp_linux\.go|InitSeccomp|buildFilter' "$REPORT"; then
  FOUND=$((FOUND + 1))
fi

# Must name the actual filtering mechanism/dependency (libseccomp binding, DefaultAction field), not
# just "BPF filter" or "SCMP_ACT_ERRNO" — the latter is already quoted verbatim in the alert log
if grep -qiE 'libseccomp|seccomp_rule|DefaultAction' "$REPORT"; then
  FOUND=$((FOUND + 1))
fi

# Must name a syscall beyond the one already given in the alert log (clone3) — forces reading the
# profile/filter code rather than echoing the alert
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

printf '{"score": %s, "passed": %s, "reason": "Identified %d/%d runc seccomp filter elements"}\n' "$SCORE" "$PASSED" "$FOUND" "$TOTAL"
