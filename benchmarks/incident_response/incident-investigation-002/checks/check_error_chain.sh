#!/usr/bin/env bash
# Checkpoint 2: Verify agent traced the error chain
set -euo pipefail

export REPORT="${WORKSPACE:-/workspace}/kubernetes/INCIDENT_REPORT.md"
if [[ ! -f "$REPORT" ]]; then
  printf '{"score": 0.0, "passed": false, "reason": "INCIDENT_REPORT.md not found"}\n'
  exit 0
fi

FOUND=0
TOTAL=4

# Repo-specific artifacts only -- the entry point (client-go/informer) is a
# given fact of the scenario; the other three hops must be tied to the
# specific file/function the agent found in the repo, not the prompt's wording.

# Must mention client-go or informer (entry point)
if grep -qiE 'client.go|informer' "$REPORT"; then
  FOUND=$((FOUND + 1))
fi

# Must cite the specific bug-location file
if grep -qiE 'cacher\.go|storage/cacher' "$REPORT"; then
  FOUND=$((FOUND + 1))
fi

# Must cite the specific reference-implementation file (correct behavior)
if grep -qiE 'etcd3/watcher\.go|etcd/etcd_watcher\.go|etcd_watcher\.go' "$REPORT"; then
  FOUND=$((FOUND + 1))
fi

# Must name the actual filter-conversion mechanism/symbols, not the generic word "filter"
if grep -qiE 'processEvent|curObjPasses|oldObjPasses|PrevObject' "$REPORT"; then
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

printf '{"score": %s, "passed": %s, "reason": "Traced %d/%d error chain components"}\n' "$SCORE" "$PASSED" "$FOUND" "$TOTAL"
