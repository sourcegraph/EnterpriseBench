#!/usr/bin/env bash
set -euo pipefail

REPORT="${WORKSPACE:-/workspace}/BLAST_RADIUS.md"
if [[ ! -f "$REPORT" ]]; then
  printf '{"score": 0.0, "passed": false, "reason": "BLAST_RADIUS.md not found"}\n'
  exit 0
fi

FOUND=0
TOTAL=2
# Repo-specific artifacts only — generic "gRPC"/"Kubernetes client" language is
# given in the prompt and must not earn credit on its own.
if grep -qiE '@grpc/grpc-js|packages/grpc-js' "$REPORT"; then FOUND=$((FOUND + 1)); fi
if grep -qiE 'kubernetes-client-js|kubernetes-client-node|kubernetes-client/javascript' "$REPORT"; then FOUND=$((FOUND + 1)); fi

SCORE=$(awk "BEGIN {printf \"%.2f\", $FOUND/$TOTAL}")
if [ "$FOUND" -ge 2 ]; then PASSED=true; else PASSED=false; fi

printf '{"score": %s, "passed": %s, "reason": "Found %d/%d dependents"}\n' "$SCORE" "$PASSED" "$FOUND" "$TOTAL"
