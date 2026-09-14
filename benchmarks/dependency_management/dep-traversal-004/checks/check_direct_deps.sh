#!/usr/bin/env bash
set -euo pipefail

REPORT="${WORKSPACE:-/workspace}/BLAST_RADIUS.md"
if [[ ! -f "$REPORT" ]]; then
  printf '{"score": 0.0, "passed": false, "reason": "BLAST_RADIUS.md not found"}\n'
  exit 0
fi

FOUND=0
TOTAL=3
# Repo-specific pinned x/net versions found in each repo's own go.mod -- naming
# the repo alone is not enough (that's already given in the prompt); the actual
# resolved version per repo only comes from reading go.mod.
if grep -qi 'kubernetes' "$REPORT" && grep -qiE 'v0\.13\.0' "$REPORT"; then FOUND=$((FOUND + 1)); fi
if grep -qi 'etcd' "$REPORT" && grep -qiE 'v0\.7\.0' "$REPORT"; then FOUND=$((FOUND + 1)); fi
if grep -qiE 'grpc.go|grpc-go' "$REPORT" && grep -qiE 'v0\.12\.0' "$REPORT"; then FOUND=$((FOUND + 1)); fi

SCORE=$(awk "BEGIN {printf \"%.2f\", $FOUND/$TOTAL}")
if [ "$FOUND" -ge 2 ]; then PASSED=true; else PASSED=false; fi

printf '{"score": %s, "passed": %s, "reason": "Found %d/%d direct dependents"}\n' "$SCORE" "$PASSED" "$FOUND" "$TOTAL"
