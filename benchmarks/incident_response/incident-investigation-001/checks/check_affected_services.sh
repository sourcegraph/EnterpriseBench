#!/usr/bin/env bash
# Checkpoint 3: Verify agent listed affected components/services
set -euo pipefail

export REPORT="${WORKSPACE:-/workspace}/kubernetes/INCIDENT_REPORT.md"
if [[ ! -f "$REPORT" ]]; then
  printf '{"score": 0.0, "passed": false, "reason": "INCIDENT_REPORT.md not found"}\n'
  exit 0
fi

FOUND=0
TOTAL=3

# Repo-specific artifacts only -- generic incident terms (apiserver, controller,
# etcd, storage) are given verbatim in the prompt and must not earn credit.

# Must cite the watch cache implementation file
if grep -qiE 'staging/src/k8s\.io/apiserver/pkg/storage/watch_cache\.go|watch_cache\.go' "$REPORT"; then
  FOUND=$((FOUND + 1))
fi

# Must cite the cacher (watch cache layer) file or the client-go informer entry point
if grep -qiE 'staging/src/k8s\.io/apiserver/pkg/storage/cacher\.go|cacher\.go|client-go.*informer|informer.*client-go' "$REPORT"; then
  FOUND=$((FOUND + 1))
fi

# Must cite the etcd3 storage/watcher path specifically
if grep -qiE 'etcd3.*watcher|watcher\.go.*etcd3|storage/etcd3' "$REPORT"; then
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
