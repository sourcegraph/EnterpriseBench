#!/usr/bin/env bash
set -euo pipefail

REPORT="${WORKSPACE:-/workspace}/BLAST_RADIUS.md"
if [[ ! -f "$REPORT" ]]; then
  printf '{"score": 0.0, "passed": false, "reason": "BLAST_RADIUS.md not found"}\n'
  exit 0
fi

FOUND=0
TOTAL=2
if grep -qi 'CVE-2020-36518' "$REPORT"; then FOUND=$((FOUND + 1)); fi
# Requires the actual vulnerable/fixed version numbers, not just the package
# name -- these only surface from reading the repos' manifests/BOM chain, not
# from the task prompt itself.
if grep -qiE '2\.12\.6\.1|2\.13\.2\.1|2\.12\.6\.0|2\.13\.0' "$REPORT"; then FOUND=$((FOUND + 1)); fi

SCORE=$(awk "BEGIN {printf \"%.2f\", $FOUND/$TOTAL}")
if [ "$FOUND" -ge 2 ]; then PASSED=true; else PASSED=false; fi

printf '{"score": %s, "passed": %s, "reason": "Identified %d/%d CVE details"}\n' "$SCORE" "$PASSED" "$FOUND" "$TOTAL"
