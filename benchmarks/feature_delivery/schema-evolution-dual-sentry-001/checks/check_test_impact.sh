#!/usr/bin/env bash
# Checkpoint 4: Agent identifies the test coverage required on both sides.
# Tokens derived from expected_solution.json :: test_impact.
set -euo pipefail

REPORT="${WORKSPACE:-/workspace}/SCHEMA_IMPACT.md"
if [[ ! -f "$REPORT" ]]; then
  printf '{"score": 0.0, "passed": false, "reason": "SCHEMA_IMPACT.md not found"}\n'
  exit 0
fi

FOUND=0
TOTAL=4
# Sentry-side schema/normalization fixture tests
if grep -qE 'event_manager' "$REPORT"; then FOUND=$((FOUND + 1)); fi
# Relay-side protocol round-trip tests
if grep -qE 'protocol/event\.rs|round[ -]?trip' "$REPORT"; then FOUND=$((FOUND + 1)); fi
# Envelope tests covering item types
if grep -qE 'envelope' "$REPORT"; then FOUND=$((FOUND + 1)); fi
# Cross-repo integration / fixture corpora exercising both normalizers
if grep -qE 'integration|fixture' "$REPORT"; then FOUND=$((FOUND + 1)); fi

SCORE=$(awk "BEGIN {printf \"%.2f\", $FOUND/$TOTAL}")
if [ "$FOUND" -ge 3 ]; then PASSED=true; else PASSED=false; fi

printf '{"score": %s, "passed": %s, "reason": "Identified %d/%d test impact areas"}\n' "$SCORE" "$PASSED" "$FOUND" "$TOTAL"
