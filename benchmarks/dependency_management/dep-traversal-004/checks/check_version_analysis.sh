#!/usr/bin/env bash
set -euo pipefail

REPORT="${WORKSPACE:-/workspace}/BLAST_RADIUS.md"
if [[ ! -f "$REPORT" ]]; then
  printf '{"score": 0.0, "passed": false, "reason": "BLAST_RADIUS.md not found"}\n'
  exit 0
fi

FOUND=0
TOTAL=2
# Requires at least two distinct actual resolved x/net versions across the
# workspace repos (kubernetes v0.13.0, grpc-go v0.12.0, etcd v0.7.0) -- these
# come only from reading go.sum/go.mod in each repo, not from the prompt.
DISTINCT=0
if grep -qiE 'v0\.13\.0' "$REPORT"; then DISTINCT=$((DISTINCT + 1)); fi
if grep -qiE 'v0\.12\.0' "$REPORT"; then DISTINCT=$((DISTINCT + 1)); fi
if grep -qiE 'v0\.7\.0' "$REPORT"; then DISTINCT=$((DISTINCT + 1)); fi
if [ "$DISTINCT" -ge 2 ]; then FOUND=$((FOUND + 1)); fi
if grep -qiE 'affected|vulnerable|patched|needs.*upgrade' "$REPORT"; then FOUND=$((FOUND + 1)); fi

SCORE=$(awk "BEGIN {printf \"%.2f\", $FOUND/$TOTAL}")
if [ "$FOUND" -ge 2 ]; then PASSED=true; else PASSED=false; fi

printf '{"score": %s, "passed": %s, "reason": "Version analysis: %d/%d criteria met"}\n' "$SCORE" "$PASSED" "$FOUND" "$TOTAL"
