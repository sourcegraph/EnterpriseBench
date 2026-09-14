#!/usr/bin/env bash
# Verifier for checkpoint: race_condition
# Task: incident-inv-dual-cortex-thanos-001
set -euo pipefail

REPORT="${WORKSPACE:-/workspace}/analysis/IMPACT_REPORT.md"

if [[ ! -f "$REPORT" ]]; then
    printf '{"score": 0.0, "passed": false, "detail": "Report not found"}\n'
    exit 0
fi

FOUND=0
TOTAL=3

# Repo-specific artifacts only — generic incident terms (race, partial,
# query, result) are given in the prompt and must not earn credit on their own.
grep -qiE 'topology change|token-claim refresh|token claim refresh' "$REPORT" && FOUND=$((FOUND + 1))
grep -qiE 'ring snapshot' "$REPORT" && FOUND=$((FOUND + 1))
grep -qiE 'duplicat.*sample|sample.*duplicat|no data' "$REPORT" && FOUND=$((FOUND + 1))

SCORE=$(awk "BEGIN {printf \"%.2f\", $FOUND/$TOTAL}")
PASSED=$([ "$FOUND" -ge 2 ] && echo true || echo false)
printf '{"score": %s, "passed": %s, "detail": "Matched %d/%d patterns"}\n' "$SCORE" "$PASSED" "$FOUND" "$TOTAL"
