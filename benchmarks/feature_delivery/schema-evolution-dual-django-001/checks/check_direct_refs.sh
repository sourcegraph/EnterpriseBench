#!/usr/bin/env bash
# Checkpoint 2: Wagtail model classes that directly depend on changed Django behavior.
# Anchored on the Wagtail model hierarchy named in the instruction (Page, StreamField,
# Orderable, RevisionMixin) and the curated GT file wagtail/models/__init__.py.
set -euo pipefail

REPORT="${WORKSPACE:-/workspace}/SCHEMA_IMPACT.md"
if [[ ! -f "$REPORT" ]]; then
  printf '{"score": 0.0, "passed": false, "reason": "SCHEMA_IMPACT.md not found"}\n'
  exit 0
fi

FOUND=0
TOTAL=4
grep -qE 'StreamField' "$REPORT" && FOUND=$((FOUND + 1)) || true
grep -qE 'Orderable' "$REPORT" && FOUND=$((FOUND + 1)) || true
grep -qE 'RevisionMixin' "$REPORT" && FOUND=$((FOUND + 1)) || true
grep -qE 'wagtail/models|models/__init__\.py|class Page|Page model' "$REPORT" && FOUND=$((FOUND + 1)) || true

SCORE=$(awk "BEGIN {printf \"%.2f\", $FOUND/$TOTAL}")
if [ "$FOUND" -ge 3 ]; then PASSED=true; else PASSED=false; fi
printf '{"score": %s, "passed": %s, "reason": "Found %d/%d direct Wagtail model references"}\n' "$SCORE" "$PASSED" "$FOUND" "$TOTAL"
