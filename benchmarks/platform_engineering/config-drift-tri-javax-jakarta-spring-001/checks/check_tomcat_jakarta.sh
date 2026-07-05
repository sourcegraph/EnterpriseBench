#!/usr/bin/env bash
# Verifier for checkpoint: tomcat_jakarta
# Task: config-drift-tri-javax-jakarta-spring-001
set -euo pipefail

REPORT="${WORKSPACE:-/workspace}/analysis/IMPACT_REPORT.md"

if [[ ! -f "$REPORT" ]]; then
    printf '{"score": 0.0, "passed": false, "detail": "Report not found"}\n'
    exit 0
fi

FOUND=0
TOTAL=2

grep -qiE 'jakarta\.servlet|javax\.servlet' "$REPORT" && FOUND=$((FOUND + 1))
grep -qiE 'Servlet|Filter|Request' "$REPORT" && FOUND=$((FOUND + 1))

SCORE=$(awk "BEGIN {printf \"%.2f\", $FOUND/$TOTAL}")
PASSED=$([ "$FOUND" -ge "$TOTAL" ] && echo true || echo false)
printf '{"score": %s, "passed": %s, "detail": "Matched %d/%d patterns"}\n' "$SCORE" "$PASSED" "$FOUND" "$TOTAL"
