#!/usr/bin/env bash
set -euo pipefail

REPORT="${WORKSPACE:-/workspace}/BLAST_RADIUS.md"
if [[ ! -f "$REPORT" ]]; then
  printf '{"score": 0.0, "passed": false, "reason": "BLAST_RADIUS.md not found"}\n'
  exit 0
fi

FOUND=0
TOTAL=3
GROUND_OK=0
if grep -qi 'CVE-2023-32681' "$REPORT"; then FOUND=$((FOUND + 1)); fi
if grep -qiE '2\.31\.0|2\.3\.0' "$REPORT"; then FOUND=$((FOUND + 1)); fi
# Repo-specific grounding: the requests version actually checked out in this
# workspace (requests/requests/__version__.py) is never stated in the prompt —
# the agent must open the repo to confirm the pinned checkout is in range.
if grep -qiE '2\.28\.0|__version__\.py' "$REPORT"; then FOUND=$((FOUND + 1)); GROUND_OK=1; fi

SCORE=$(awk "BEGIN {printf \"%.2f\", $FOUND/$TOTAL}")
if [ "$GROUND_OK" -eq 1 ] && [ "$FOUND" -ge 2 ]; then PASSED=true; else PASSED=false; fi

printf '{"score": %s, "passed": %s, "reason": "Identified %d/%d CVE details"}\n' "$SCORE" "$PASSED" "$FOUND" "$TOTAL"
