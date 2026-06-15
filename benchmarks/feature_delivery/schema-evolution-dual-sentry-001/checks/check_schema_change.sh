#!/usr/bin/env bash
# Checkpoint 1: Agent identifies the Sentry/Relay cross-repo schema boundary.
# Tokens derived from expected_solution.json :: schema_change.
set -euo pipefail

REPORT="${WORKSPACE:-/workspace}/SCHEMA_IMPACT.md"
if [[ ! -f "$REPORT" ]]; then
  printf '{"score": 0.0, "passed": false, "reason": "SCHEMA_IMPACT.md not found"}\n'
  exit 0
fi

FOUND=0
TOTAL=2
# Sentry-side Django model schema definition
if grep -qE 'sentry/models/project\.py|models/project\.py' "$REPORT"; then FOUND=$((FOUND + 1)); fi
# Relay-side Rust protocol schema (path differs by Relay version: relay-general vs relay-event-schema)
if grep -qE 'protocol/event\.rs' "$REPORT"; then FOUND=$((FOUND + 1)); fi

SCORE=$(awk "BEGIN {printf \"%.2f\", $FOUND/$TOTAL}")
if [ "$FOUND" -ge 2 ]; then PASSED=true; else PASSED=false; fi

printf '{"score": %s, "passed": %s, "reason": "Identified %d/%d schema change elements"}\n' "$SCORE" "$PASSED" "$FOUND" "$TOTAL"
