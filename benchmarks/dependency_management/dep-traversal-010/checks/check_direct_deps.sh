#!/usr/bin/env bash
set -euo pipefail

REPORT="${WORKSPACE:-/workspace}/BLAST_RADIUS.md"
if [[ ! -f "$REPORT" ]]; then
  printf '{"score": 0.0, "passed": false, "reason": "BLAST_RADIUS.md not found"}\n'
  exit 0
fi

FOUND=0
TOTAL=2
# The actual module/manifest that directly declares the jackson-databind
# dependency in each framework repo -- not just the framework's name (which
# is already given in the task's environment listing).
if grep -qiE 'dropwizard-jackson' "$REPORT"; then FOUND=$((FOUND + 1)); fi
if grep -qiE 'spring-boot-starter-web' "$REPORT"; then FOUND=$((FOUND + 1)); fi

SCORE=$(awk "BEGIN {printf \"%.2f\", $FOUND/$TOTAL}")
if [ "$FOUND" -ge 2 ]; then PASSED=true; else PASSED=false; fi

printf '{"score": %s, "passed": %s, "reason": "Found %d/%d dependents"}\n' "$SCORE" "$PASSED" "$FOUND" "$TOTAL"
