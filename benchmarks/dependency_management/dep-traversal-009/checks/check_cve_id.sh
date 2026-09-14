#!/usr/bin/env bash
set -euo pipefail

REPORT="${WORKSPACE:-/workspace}/BLAST_RADIUS.md"
if [[ ! -f "$REPORT" ]]; then
  printf '{"score": 0.0, "passed": false, "reason": "BLAST_RADIUS.md not found"}\n'
  exit 0
fi

FOUND=0
TOTAL=2
# The advisory ID is no longer named in the prompt — it must be identified.
if grep -qi 'CVE-2023-43804' "$REPORT"; then FOUND=$((FOUND + 1)); fi
# Both concrete version boundaries from the advisory: the 1.x fix (1.26.17)
# and a 2.x-line version number (2.0.0-2.0.6). Neither appears in the prompt.
if grep -qiE '1\.26\.17' "$REPORT" && grep -qiE '2\.0\.[0-6]' "$REPORT"; then FOUND=$((FOUND + 1)); fi

SCORE=$(awk "BEGIN {printf \"%.2f\", $FOUND/$TOTAL}")
if [ "$FOUND" -ge 2 ]; then PASSED=true; else PASSED=false; fi

printf '{"score": %s, "passed": %s, "reason": "Identified %d/%d CVE details"}\n' "$SCORE" "$PASSED" "$FOUND" "$TOTAL"
