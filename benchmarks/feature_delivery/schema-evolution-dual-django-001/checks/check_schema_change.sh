#!/usr/bin/env bash
# Checkpoint 1: Agent identifies the Django 5.0 field/Meta changes that affect Wagtail.
# Tokens derived from expected_solution.json :: schema_change.
set -euo pipefail

REPORT="${WORKSPACE:-/workspace}/SCHEMA_IMPACT.md"
if [[ ! -f "$REPORT" ]]; then
  printf '{"score": 0.0, "passed": false, "reason": "SCHEMA_IMPACT.md not found"}\n'
  exit 0
fi

FOUND=0
TOTAL=2
# Primary Django change site: Field.contribute_to_class in db/models/fields/__init__.py
if grep -qE 'db/models/fields/__init__\.py|contribute_to_class' "$REPORT"; then FOUND=$((FOUND + 1)); fi
# Meta handling + the new GeneratedField (db/models/options.py)
if grep -qE 'db/models/options\.py|GeneratedField' "$REPORT"; then FOUND=$((FOUND + 1)); fi

SCORE=$(awk "BEGIN {printf \"%.2f\", $FOUND/$TOTAL}")
if [ "$FOUND" -ge 2 ]; then PASSED=true; else PASSED=false; fi

printf '{"score": %s, "passed": %s, "reason": "Identified %d/%d schema change elements"}\n' "$SCORE" "$PASSED" "$FOUND" "$TOTAL"
