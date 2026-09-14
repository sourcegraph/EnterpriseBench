#!/usr/bin/env bash
# Checkpoint 4: Verify agent proposed correct remediation
set -euo pipefail

export REPORT="${WORKSPACE:-/workspace}/kubernetes/INCIDENT_REPORT.md"
if [[ ! -f "$REPORT" ]]; then
  printf '{"score": 0.0, "passed": false, "reason": "INCIDENT_REPORT.md not found"}\n'
  exit 0
fi

FOUND=0
TOTAL=2

# Repo-specific artifacts only -- generic phrases like "set resourceVersion" or
# "reference implementation" are given in the prompt and must not earn credit.

# Must locate the fix in the specific bug-location file
if grep -qiE 'cacher\.go|storage/cacher' "$REPORT"; then
  FOUND=$((FOUND + 1))
fi

# Must reference the specific correct-pattern artifact: the reference
# implementation file, the versioner mechanism, or the whitebox test file
if grep -qiE 'etcd3/watcher\.go|etcd/etcd_watcher\.go|etcd_watcher\.go|versioner|cacher_whitebox_test' "$REPORT"; then
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

printf '{"score": %s, "passed": %s, "reason": "Remediation quality: %d/%d key elements"}\n' "$SCORE" "$PASSED" "$FOUND" "$TOTAL"
