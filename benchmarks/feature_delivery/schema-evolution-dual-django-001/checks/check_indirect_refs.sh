#!/usr/bin/env bash
# Checkpoint 3: Indirect dependencies / impact chain — the StreamField source file and
# the Wagtail operations (migrations, queries, admin serialization) the instruction
# asks the impact chain to cover.
set -euo pipefail

REPORT="${WORKSPACE:-/workspace}/SCHEMA_IMPACT.md"
if [[ ! -f "$REPORT" ]]; then
  printf '{"score": 0.0, "passed": false, "reason": "SCHEMA_IMPACT.md not found"}\n'
  exit 0
fi

FOUND=0
TOTAL=4
grep -qE 'fields/streamfield\.py|fields/streamfield' "$REPORT" && FOUND=$((FOUND + 1)) || true
grep -qiE 'migration' "$REPORT" && FOUND=$((FOUND + 1)) || true
grep -qiE 'admin' "$REPORT" && FOUND=$((FOUND + 1)) || true
grep -qiE 'queries|queryset|query|serializ' "$REPORT" && FOUND=$((FOUND + 1)) || true

SCORE=$(awk "BEGIN {printf \"%.2f\", $FOUND/$TOTAL}")
if [ "$FOUND" -ge 2 ]; then PASSED=true; else PASSED=false; fi
printf '{"score": %s, "passed": %s, "reason": "Found %d/%d indirect dependency / impact-chain elements"}\n' "$SCORE" "$PASSED" "$FOUND" "$TOTAL"
