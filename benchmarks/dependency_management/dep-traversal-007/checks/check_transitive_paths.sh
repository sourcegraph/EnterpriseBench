#!/usr/bin/env bash
set -euo pipefail

REPORT="${WORKSPACE:-/workspace}/BLAST_RADIUS.md"
if [[ ! -f "$REPORT" ]]; then
  printf '{"score": 0.0, "passed": false, "reason": "BLAST_RADIUS.md not found"}\n'
  exit 0
fi

FOUND=0
TOTAL=2
# protobufjs -> grpc-js hop: the monorepo subpackage manifest, not the generic
# "gRPC"/"prototype pollution" language already given in the prompt.
if grep -qiE '@grpc/grpc-js|packages/grpc-js' "$REPORT"; then FOUND=$((FOUND + 1)); fi
# grpc-js -> downstream consumer hop: the actual repo the chain terminates in.
if grep -qiE 'kubernetes-client-js|kubernetes-client-node|kubernetes-client/javascript' "$REPORT"; then FOUND=$((FOUND + 1)); fi

SCORE=$(awk "BEGIN {printf \"%.2f\", $FOUND/$TOTAL}")
if [ "$FOUND" -ge 2 ]; then PASSED=true; else PASSED=false; fi

printf '{"score": %s, "passed": %s, "reason": "Traced %d/%d transitive paths"}\n' "$SCORE" "$PASSED" "$FOUND" "$TOTAL"
