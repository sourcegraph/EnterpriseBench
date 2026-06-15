#!/usr/bin/env bash
# Checkpoint 2: Agent finds the code paths that directly consume the schema on each side.
# Tokens derived from expected_solution.json :: direct_refs.
set -euo pipefail

REPORT="${WORKSPACE:-/workspace}/SCHEMA_IMPACT.md"
if [[ ! -f "$REPORT" ]]; then
  printf '{"score": 0.0, "passed": false, "reason": "SCHEMA_IMPACT.md not found"}\n'
  exit 0
fi

FOUND=0
TOTAL=4
# Sentry-side project/config model
if grep -qE 'models/project\.py' "$REPORT"; then FOUND=$((FOUND + 1)); fi
# Sentry-side event validation/normalization
if grep -qE 'event_manager' "$REPORT"; then FOUND=$((FOUND + 1)); fi
# Relay-side canonical Rust schema source-of-truth
if grep -qE 'protocol/event\.rs' "$REPORT"; then FOUND=$((FOUND + 1)); fi
# Relay-side envelope validation consumer (rejects mismatched events)
if grep -qE 'envelope\.rs|envelope' "$REPORT"; then FOUND=$((FOUND + 1)); fi

SCORE=$(awk "BEGIN {printf \"%.2f\", $FOUND/$TOTAL}")
if [ "$FOUND" -ge 3 ]; then PASSED=true; else PASSED=false; fi

printf '{"score": %s, "passed": %s, "reason": "Found %d/%d direct reference locations"}\n' "$SCORE" "$PASSED" "$FOUND" "$TOTAL"
