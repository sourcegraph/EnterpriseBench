#!/usr/bin/env bash
set -euo pipefail

REPORT="${WORKSPACE:-/workspace}/analysis/IMPACT_REPORT.md"
if [[ ! -f "$REPORT" ]]; then
  printf '{"score": 0.0, "passed": false, "reason": "IMPACT_REPORT.md not found"}\n'
  exit 0
fi

CONTENT=$(cat "$REPORT" | tr '[:upper:]' '[:lower:]')
FOUND=0
TOTAL=3
# Must identify runtime (not compile) breakage — the prompt never uses this
# classification language, so this is the agent's own conclusion.
if echo "$CONTENT" | grep -qE 'runtime|silent|not.*compile|behavior.*change'; then FOUND=$((FOUND + 1)); fi
# Must identify the delayed/silent-detection angle
if echo "$CONTENT" | grep -qE 'month|delay|late|subtle|silent.*fail'; then FOUND=$((FOUND + 1)); fi
# Must ground the classification in a specific consumer/producer file — a
# repo-specific artifact the prompt does not name.
if echo "$CONTENT" | grep -qE 'internal/status/status\.go|status_ext_test\.go|status_equal\.go|internal/testutils'; then FOUND=$((FOUND + 1)); fi

SCORE=$(awk "BEGIN {printf \"%.2f\", $FOUND/$TOTAL}")
if [ "$FOUND" -ge 2 ]; then PASSED=true; else PASSED=false; fi

printf '{"score": %s, "passed": %s, "reason": "Classified %d/%d breakage aspects"}\n' "$SCORE" "$PASSED" "$FOUND" "$TOTAL"
