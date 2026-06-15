#!/usr/bin/env bash
# Checkpoint 4: Agent identifies test impact of the schema changes.
# Tokens derived from expected_solution.json :: test_impact.
set -euo pipefail

REPORT="${WORKSPACE:-/workspace}/SCHEMA_IMPACT.md"
if [[ ! -f "$REPORT" ]]; then
  printf '{"score": 0.0, "passed": false, "reason": "SCHEMA_IMPACT.md not found"}\n'
  exit 0
fi

FOUND=0
TOTAL=4
# Tests must cover the Django field internals / GeneratedField behavior
if grep -qE 'db/models/fields|contribute_to_class|GeneratedField' "$REPORT"; then FOUND=$((FOUND + 1)); fi
# Page subclass migration generation under Django 5.0
if grep -qE 'migration' "$REPORT"; then FOUND=$((FOUND + 1)); fi
# StreamField deconstruction stability tests
if grep -qE 'deconstruct|StreamField' "$REPORT"; then FOUND=$((FOUND + 1)); fi
# Test matrix across Django 4.2 / 5.0 (+ Wagtail 5.2)
if grep -qE '4\.2|5\.0|matrix' "$REPORT"; then FOUND=$((FOUND + 1)); fi

SCORE=$(awk "BEGIN {printf \"%.2f\", $FOUND/$TOTAL}")
if [ "$FOUND" -ge 3 ]; then PASSED=true; else PASSED=false; fi

printf '{"score": %s, "passed": %s, "reason": "Identified %d/%d test impact areas"}\n' "$SCORE" "$PASSED" "$FOUND" "$TOTAL"
