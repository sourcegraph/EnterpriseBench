#!/usr/bin/env bash
# Checkpoint 1: Sentry Django model/event-schema definitions that evolved.
# Anchored on the Sentry modules named in the instruction and the curated GT files
# (src/sentry/models/project.py, src/sentry/event_manager.py).
set -euo pipefail

REPORT="${WORKSPACE:-/workspace}/SCHEMA_IMPACT.md"
if [[ ! -f "$REPORT" ]]; then
  printf '{"score": 0.0, "passed": false, "reason": "SCHEMA_IMPACT.md not found"}\n'
  exit 0
fi

FOUND=0
TOTAL=4
grep -qE 'event_manager' "$REPORT" && FOUND=$((FOUND + 1)) || true
grep -qE 'sentry/models|models/project\.py' "$REPORT" && FOUND=$((FOUND + 1)) || true
grep -qiE 'data categor|event type|project config' "$REPORT" && FOUND=$((FOUND + 1)) || true
grep -qiE 'schema' "$REPORT" && FOUND=$((FOUND + 1)) || true

SCORE=$(awk "BEGIN {printf \"%.2f\", $FOUND/$TOTAL}")
if [ "$FOUND" -ge 2 ]; then PASSED=true; else PASSED=false; fi
printf '{"score": %s, "passed": %s, "reason": "Identified %d/%d Sentry schema-change elements"}\n' "$SCORE" "$PASSED" "$FOUND" "$TOTAL"
