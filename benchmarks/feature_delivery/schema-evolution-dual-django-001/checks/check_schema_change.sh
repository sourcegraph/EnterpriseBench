#!/usr/bin/env bash
# Checkpoint 1: Django 5.0 model/field internal changes relevant to Wagtail.
# Anchored on the concrete Django 5.0 APIs named in the task instruction and the
# curated ground-truth source files (django/db/models/fields/__init__.py, options.py).
set -euo pipefail

REPORT="${WORKSPACE:-/workspace}/SCHEMA_IMPACT.md"
if [[ ! -f "$REPORT" ]]; then
  printf '{"score": 0.0, "passed": false, "reason": "SCHEMA_IMPACT.md not found"}\n'
  exit 0
fi

FOUND=0
TOTAL=4
grep -qE 'GeneratedField' "$REPORT" && FOUND=$((FOUND + 1)) || true
grep -qE 'contribute_to_class' "$REPORT" && FOUND=$((FOUND + 1)) || true
grep -qE 'db/models/fields|fields/__init__\.py' "$REPORT" && FOUND=$((FOUND + 1)) || true
grep -qE 'db/models/options\.py|options\.py|class Meta|model Meta|Meta handling' "$REPORT" && FOUND=$((FOUND + 1)) || true

SCORE=$(awk "BEGIN {printf \"%.2f\", $FOUND/$TOTAL}")
if [ "$FOUND" -ge 2 ]; then PASSED=true; else PASSED=false; fi
printf '{"score": %s, "passed": %s, "reason": "Identified %d/%d Django 5.0 schema-change elements"}\n' "$SCORE" "$PASSED" "$FOUND" "$TOTAL"
