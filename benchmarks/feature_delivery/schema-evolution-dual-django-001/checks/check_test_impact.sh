#!/usr/bin/env bash
# Checkpoint 4: Migration-coordination / test impact across both repos. The instruction's
# 4th deliverable is the migration files in both repos that need coordination; a complete
# answer also names the tests affected.
set -euo pipefail

REPORT="${WORKSPACE:-/workspace}/SCHEMA_IMPACT.md"
if [[ ! -f "$REPORT" ]]; then
  printf '{"score": 0.0, "passed": false, "reason": "SCHEMA_IMPACT.md not found"}\n'
  exit 0
fi

FOUND=0
TOTAL=3
grep -qiE 'migration' "$REPORT" && FOUND=$((FOUND + 1)) || true
grep -qiE 'test' "$REPORT" && FOUND=$((FOUND + 1)) || true
# Cross-repo coordination: both repos referenced by name
if grep -qiE 'django' "$REPORT" && grep -qiE 'wagtail' "$REPORT"; then FOUND=$((FOUND + 1)); fi

SCORE=$(awk "BEGIN {printf \"%.2f\", $FOUND/$TOTAL}")
if [ "$FOUND" -ge 2 ]; then PASSED=true; else PASSED=false; fi
printf '{"score": %s, "passed": %s, "reason": "Identified %d/%d migration-coordination / test-impact elements"}\n' "$SCORE" "$PASSED" "$FOUND" "$TOTAL"
