#!/usr/bin/env bash
set -euo pipefail

REPORT="${WORKSPACE:-/workspace}/BLAST_RADIUS.md"
if [[ ! -f "$REPORT" ]]; then
  printf '{"score": 0.0, "passed": false, "reason": "BLAST_RADIUS.md not found"}\n'
  exit 0
fi

FOUND=0
TOTAL=2
# Requires an actual manifest file citation (the build-file extension), not
# just prose about "a build file" -- proves a specific file was opened.
if grep -qiE '\.xml|\.gradle' "$REPORT"; then FOUND=$((FOUND + 1)); fi
# Requires citing the concrete resolved/fixed version number AND classifying
# it, not just generic prose about being "in range".
if grep -qiE '2\.12\.6\.1|2\.13\.2\.1|2\.12\.6\.0|2\.13\.0' "$REPORT" && \
   grep -qiE 'affected|vulnerable|patched|needs.*upgrade' "$REPORT"; then
  FOUND=$((FOUND + 1))
fi

SCORE=$(awk "BEGIN {printf \"%.2f\", $FOUND/$TOTAL}")
if [ "$FOUND" -ge 2 ]; then PASSED=true; else PASSED=false; fi

printf '{"score": %s, "passed": %s, "reason": "Version analysis: %d/%d criteria met"}\n' "$SCORE" "$PASSED" "$FOUND" "$TOTAL"
