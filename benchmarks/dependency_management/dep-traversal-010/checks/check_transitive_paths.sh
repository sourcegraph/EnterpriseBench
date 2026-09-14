#!/usr/bin/env bash
set -euo pipefail

REPORT="${WORKSPACE:-/workspace}/BLAST_RADIUS.md"
if [[ ! -f "$REPORT" ]]; then
  printf '{"score": 0.0, "passed": false, "reason": "BLAST_RADIUS.md not found"}\n'
  exit 0
fi

FOUND=0
TOTAL=3
# Structural: an actual A -> B -> C chain (at least two hops), not just a
# claim that "a chain exists".
if grep -qE '(->|-->|\xe2\x86\x92).*(->|-->|\xe2\x86\x92)' "$REPORT"; then FOUND=$((FOUND + 1)); fi
# The real module that centrally pins jackson's version for Spring Boot.
if grep -qiE 'spring-boot-dependencies' "$REPORT"; then FOUND=$((FOUND + 1)); fi
# The real module that centrally pins jackson's version for Dropwizard.
if grep -qiE 'dropwizard-bom' "$REPORT"; then FOUND=$((FOUND + 1)); fi

SCORE=$(awk "BEGIN {printf \"%.2f\", $FOUND/$TOTAL}")
if [ "$FOUND" -ge 2 ]; then PASSED=true; else PASSED=false; fi

printf '{"score": %s, "passed": %s, "reason": "Traced %d/%d transitive paths"}\n' "$SCORE" "$PASSED" "$FOUND" "$TOTAL"
