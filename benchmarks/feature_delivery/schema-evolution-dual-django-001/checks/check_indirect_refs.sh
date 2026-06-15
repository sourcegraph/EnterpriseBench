#!/usr/bin/env bash
# Checkpoint 3: Agent traces indirect schema dependencies (admin, migrations, subclasses).
# Tokens derived from expected_solution.json :: indirect_refs.
set -euo pipefail

REPORT="${WORKSPACE:-/workspace}/SCHEMA_IMPACT.md"
if [[ ! -f "$REPORT" ]]; then
  printf '{"score": 0.0, "passed": false, "reason": "SCHEMA_IMPACT.md not found"}\n'
  exit 0
fi

FOUND=0
TOTAL=6
# Model Meta machinery / field introspection
if grep -qE '_meta|private_fields|get_field' "$REPORT"; then FOUND=$((FOUND + 1)); fi
# Admin surfaces FieldError when generated fields land differently in _meta
if grep -qE 'FieldError' "$REPORT"; then FOUND=$((FOUND + 1)); fi
# StreamField.deconstruct producing different migration output
if grep -qE 'deconstruct' "$REPORT"; then FOUND=$((FOUND + 1)); fi
# Migration generation / makemigrations diff
if grep -qE 'makemigrations|migration' "$REPORT"; then FOUND=$((FOUND + 1)); fi
# Wagtail admin serialization path
if grep -qE 'admin' "$REPORT"; then FOUND=$((FOUND + 1)); fi
# User-defined Page subclasses inherit the changed Meta machinery
if grep -qE 'subclass' "$REPORT"; then FOUND=$((FOUND + 1)); fi

SCORE=$(awk "BEGIN {printf \"%.2f\", $FOUND/$TOTAL}")
if [ "$FOUND" -ge 4 ]; then PASSED=true; else PASSED=false; fi

printf '{"score": %s, "passed": %s, "reason": "Found %d/%d indirect reference locations"}\n' "$SCORE" "$PASSED" "$FOUND" "$TOTAL"
