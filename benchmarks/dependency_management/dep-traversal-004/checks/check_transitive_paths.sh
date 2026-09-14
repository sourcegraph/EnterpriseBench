#!/usr/bin/env bash
set -euo pipefail

REPORT="${WORKSPACE:-/workspace}/BLAST_RADIUS.md"
if [[ ! -f "$REPORT" ]]; then
  printf '{"score": 0.0, "passed": false, "reason": "BLAST_RADIUS.md not found"}\n'
  exit 0
fi

FOUND=0
TOTAL=3
# Repo-grounded edges only -- generic "x/net...grpc" style co-mentions are
# already implied by the prompt's repo list and must not earn credit alone.
# Path: x/net -> grpc-go, grounded in grpc-go's real module identity
# (google.golang.org/grpc) which only surfaces by reading grpc-go/go.mod.
if grep -qiE 'google\.golang\.org/grpc' "$REPORT"; then FOUND=$((FOUND + 1)); fi
# Path: grpc-go -> etcd, grounded in etcd's actual pinned grpc version
# (v1.41.0), which only surfaces by reading etcd/go.mod.
if grep -qiE 'v1\.41\.0' "$REPORT"; then FOUND=$((FOUND + 1)); fi
# Path: x/net -> kubernetes, grounded in kubernetes' actual pinned x/net
# version (v0.13.0), which only surfaces by reading kubernetes/go.mod.
if grep -qiE 'v0\.13\.0' "$REPORT"; then FOUND=$((FOUND + 1)); fi

SCORE=$(awk "BEGIN {printf \"%.2f\", $FOUND/$TOTAL}")
if [ "$FOUND" -ge 2 ]; then PASSED=true; else PASSED=false; fi

printf '{"score": %s, "passed": %s, "reason": "Traced %d/%d transitive paths"}\n' "$SCORE" "$PASSED" "$FOUND" "$TOTAL"
