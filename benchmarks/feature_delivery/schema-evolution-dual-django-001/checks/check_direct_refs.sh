#!/usr/bin/env bash
# Checkpoint 2: Agent finds Wagtail's direct dependents on the changed Django field internals.
# Tokens derived from expected_solution.json :: direct_refs.
set -euo pipefail

REPORT="${WORKSPACE:-/workspace}/SCHEMA_IMPACT.md"
if [[ ! -f "$REPORT" ]]; then
  printf '{"score": 0.0, "passed": false, "reason": "SCHEMA_IMPACT.md not found"}\n'
  exit 0
fi

FOUND=0
TOTAL=4
# Page hierarchy module
if grep -qE 'wagtail/models' "$REPORT"; then FOUND=$((FOUND + 1)); fi
# StreamField custom Field subclass
if grep -qE 'wagtail/fields|StreamField' "$REPORT"; then FOUND=$((FOUND + 1)); fi
# Page-hierarchy classes that override Meta / call contribute_to_class
if grep -qE 'Orderable|RevisionMixin' "$REPORT"; then FOUND=$((FOUND + 1)); fi
# Reference back to the upstream Django surface
if grep -qE 'db/models/fields/__init__\.py|db/models/options\.py|contribute_to_class' "$REPORT"; then FOUND=$((FOUND + 1)); fi

SCORE=$(awk "BEGIN {printf \"%.2f\", $FOUND/$TOTAL}")
if [ "$FOUND" -ge 3 ]; then PASSED=true; else PASSED=false; fi

printf '{"score": %s, "passed": %s, "reason": "Found %d/%d direct reference locations"}\n' "$SCORE" "$PASSED" "$FOUND" "$TOTAL"
