#!/usr/bin/env bash
set -euo pipefail

REPORT="${WORKSPACE:-/workspace}/BLAST_RADIUS.md"
if [[ ! -f "$REPORT" ]]; then
  printf '{"score": 0.0, "passed": false, "reason": "BLAST_RADIUS.md not found"}\n'
  exit 0
fi

FOUND=0
TOTAL=2
if grep -qi 'CVE-2023-39325' "$REPORT"; then FOUND=$((FOUND + 1)); fi
# Repo-grounded confirmation: at least one actual resolved x/net version pinned
# in a workspace repo's go.mod (kubernetes v0.13.0, grpc-go v0.12.0, etcd v0.7.0).
# These exact values are not in the prompt -- only reading go.mod surfaces them.
if grep -qiE 'v0\.13\.0|v0\.12\.0|v0\.7\.0' "$REPORT"; then FOUND=$((FOUND + 1)); fi

SCORE=$(awk "BEGIN {printf \"%.2f\", $FOUND/$TOTAL}")
if [ "$FOUND" -ge 2 ]; then PASSED=true; else PASSED=false; fi

printf '{"score": %s, "passed": %s, "reason": "Identified %d/%d CVE details"}\n' "$SCORE" "$PASSED" "$FOUND" "$TOTAL"
