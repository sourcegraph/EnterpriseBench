#!/usr/bin/env bash
set -euo pipefail

REPORT="${WORKSPACE:-/workspace}/BLAST_RADIUS.md"
if [[ ! -f "$REPORT" ]]; then
  printf '{"score": 0.0, "passed": false, "reason": "BLAST_RADIUS.md not found"}\n'
  exit 0
fi

FOUND=0
TOTAL=3
# Repo-specific artifact: naming the service alone is given in the prompt.
# Credit requires citing the manifest (go.mod) where that repo declares the
# dependency, per expected_solution.json's "cite the manifest file" criterion.
if grep -qiE 'prometheus.*go\.mod|go\.mod.*prometheus' "$REPORT"; then FOUND=$((FOUND + 1)); fi
if grep -qiE 'consul.*go\.mod|go\.mod.*consul' "$REPORT"; then FOUND=$((FOUND + 1)); fi
if grep -qiE 'vault.*go\.mod|go\.mod.*vault' "$REPORT"; then FOUND=$((FOUND + 1)); fi

SCORE=$(awk "BEGIN {printf \"%.2f\", $FOUND/$TOTAL}")
if [ "$FOUND" -ge 2 ]; then PASSED=true; else PASSED=false; fi

printf '{"score": %s, "passed": %s, "reason": "Found %d/%d direct dependents"}\n' "$SCORE" "$PASSED" "$FOUND" "$TOTAL"
