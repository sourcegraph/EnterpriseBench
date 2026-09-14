#!/usr/bin/env bash
set -euo pipefail

REPORT="${WORKSPACE:-/workspace}/BLAST_RADIUS.md"
if [[ ! -f "$REPORT" ]]; then
  printf '{"score": 0.0, "passed": false, "reason": "BLAST_RADIUS.md not found"}\n'
  exit 0
fi

FOUND=0
TOTAL=3
# Repo-specific artifacts only — the prompt never states the actual DAG
# structure or an unmarshal call site, so these require real investigation.
# 1) direct edge protobuf -> grpc-go, shown as an explicit chain (not just co-mention)
if grep -qiE 'protobuf.*(->|→).*grpc-go|grpc-go.*(->|→).*protobuf' "$REPORT"; then FOUND=$((FOUND + 1)); fi
# 2) transitive edge through istio via one of the real intermediaries
if grep -qiE '(grpc-go|go-control-plane|control-plane).*(->|→).*istio|istio.*(->|→).*(grpc-go|go-control-plane|control-plane)' "$REPORT"; then FOUND=$((FOUND + 1)); fi
# 3) concrete unmarshal call site evidence, not just the generic terms Any/protojson/jsonpb
if grep -qiE '\.go:[0-9]+|protojson\.Unmarshal|jsonpb\.Unmarshal(er)?|xdsclient/bootstrap|pkg/conversion/struct\.go|pkg/server/v3/gateway\.go|balancer/rls/config\.go' "$REPORT"; then FOUND=$((FOUND + 1)); fi

SCORE=$(awk "BEGIN {printf \"%.2f\", $FOUND/$TOTAL}")
if [ "$FOUND" -ge 2 ]; then PASSED=true; else PASSED=false; fi

printf '{"score": %s, "passed": %s, "reason": "Traced %d/%d transitive paths"}\n' "$SCORE" "$PASSED" "$FOUND" "$TOTAL"
