#!/usr/bin/env bash
# Checkpoint 2: Verify impact classification
set -euo pipefail

REPORT="${WORKSPACE:-/workspace}/pnpm/IMPACT_REPORT.md"
if [[ ! -f "$REPORT" ]]; then
  printf '{"score": 0.0, "passed": false, "reason": "IMPACT_REPORT.md not found"}\n'
  exit 0
fi

CONTENT=$(cat "$REPORT" | tr '[:upper:]' '[:lower:]')

FOUND=0
TOTAL=3
# "major" alone is already stated in the prompt ("still major if their
# behavior changes") — require it alongside repo-specific grounding that only
# comes from reading the changed source, not the instruction text.
if echo "$CONTENT" | grep -q 'major'; then FOUND=$((FOUND + 1)); fi
# The specific constant that was bumped — only visible in packages/constants/src/index.ts.
if echo "$CONTENT" | grep -qE 'lockfile_version'; then FOUND=$((FOUND + 1)); fi
# The v5->v6 migration path — only visible in the lockfile-file source, not the prompt.
if echo "$CONTENT" | grep -qE 'inlinespecifierslockfileconverters|lockfile-file/src/experiments'; then FOUND=$((FOUND + 1)); fi

if [ "$TOTAL" -gt 0 ]; then
  SCORE=$(awk "BEGIN {printf \"%.2f\", $FOUND/$TOTAL}")
else
  SCORE="0.00"
fi
if [ "$FOUND" -ge 2 ]; then PASSED=true; else PASSED=false; fi

printf '{"score": %s, "passed": %s, "reason": "Classified impact with %d/%d grounded signals"}\n' "$SCORE" "$PASSED" "$FOUND" "$TOTAL"
