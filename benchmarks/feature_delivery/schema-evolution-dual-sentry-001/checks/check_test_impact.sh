#!/usr/bin/env bash
# Checkpoint 4: Impact on the event ingestion pipeline (the instruction's 4th deliverable),
# plus test impact across the two services.
set -euo pipefail

REPORT="${WORKSPACE:-/workspace}/SCHEMA_IMPACT.md"
if [[ ! -f "$REPORT" ]]; then
  printf '{"score": 0.0, "passed": false, "reason": "SCHEMA_IMPACT.md not found"}\n'
  exit 0
fi

FOUND=0
TOTAL=3
grep -qiE 'ingest|event processing|pipeline' "$REPORT" && FOUND=$((FOUND + 1)) || true
grep -qiE 'test' "$REPORT" && FOUND=$((FOUND + 1)) || true
# Cross-repo coordination: both services referenced by name
if grep -qiE 'sentry' "$REPORT" && grep -qiE 'relay' "$REPORT"; then FOUND=$((FOUND + 1)); fi

SCORE=$(awk "BEGIN {printf \"%.2f\", $FOUND/$TOTAL}")
if [ "$FOUND" -ge 2 ]; then PASSED=true; else PASSED=false; fi
printf '{"score": %s, "passed": %s, "reason": "Identified %d/%d ingestion-impact / test-impact elements"}\n' "$SCORE" "$PASSED" "$FOUND" "$TOTAL"
