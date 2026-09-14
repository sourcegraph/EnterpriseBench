#!/usr/bin/env bash
# Checkpoint 3: Verify agent listed affected services
set -euo pipefail

export REPORT="${WORKSPACE:-/workspace}/kubernetes/INCIDENT_REPORT.md"
if [[ ! -f "$REPORT" ]]; then
  printf '{"score": 0.0, "passed": false, "reason": "INCIDENT_REPORT.md not found"}\n'
  exit 0
fi

FOUND=0
TOTAL=3

# Repo-specific artifacts only -- generic incident terms (apiserver, informer,
# namespace-scoped watch) are given in the prompt and must not earn credit.

# Must cite the actual bug-location file
if grep -qiE 'cacher\.go|storage/cacher' "$REPORT"; then
  FOUND=$((FOUND + 1))
fi

# Must cite at least one of the reference-implementation files (correct behavior)
if grep -qiE 'etcd3/watcher\.go|etcd/etcd_watcher\.go|etcd_watcher\.go' "$REPORT"; then
  FOUND=$((FOUND + 1))
fi

# Must name the specific function/consumer, not just the generic terms
if grep -qiE 'processEvent|client.go.*informer|informer.*controller' "$REPORT"; then
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

printf '{"score": %s, "passed": %s, "reason": "Identified %d/%d affected services"}\n' "$SCORE" "$PASSED" "$FOUND" "$TOTAL"
