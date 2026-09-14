#!/usr/bin/env bash
# Verifier for checkpoint: log4j_bridges
# Task: dep-graph-dual-log4j-spring-001
set -euo pipefail

REPORT="${WORKSPACE:-/workspace}/analysis/IMPACT_REPORT.md"

if [[ ! -f "$REPORT" ]]; then
    printf '{"score": 0.0, "passed": false, "detail": "Report not found"}\n'
    exit 0
fi

FOUND=0
TOTAL=3

# Cites a canonical file by basename (repo-specific artifact, not named in the prompt)
grep -qiE 'LogManager\.java|LoggingSystem\.java|Log4J2LoggingSystem\.java' "$REPORT" && FOUND=$((FOUND + 1))
# Mentions all three canonical concept tokens — LogManager is the discriminating
# one (log4j-to-slf4j / log4j-api are already named in the prompt on their own)
grep -qi 'log4j-to-slf4j' "$REPORT" && grep -qi 'log4j-api' "$REPORT" && grep -qi 'LogManager' "$REPORT" && FOUND=$((FOUND + 1))
# States the dependency relationship type
grep -qiE 'logging[_ -]?bridge' "$REPORT" && FOUND=$((FOUND + 1))

SCORE=$(awk "BEGIN {printf \"%.2f\", $FOUND/$TOTAL}")
PASSED=$([ "$FOUND" -ge "$TOTAL" ] && echo true || echo false)
printf '{"score": %s, "passed": %s, "detail": "Matched %d/%d patterns"}\n' "$SCORE" "$PASSED" "$FOUND" "$TOTAL"
