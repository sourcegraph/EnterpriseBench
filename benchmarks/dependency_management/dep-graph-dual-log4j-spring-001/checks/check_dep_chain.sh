#!/usr/bin/env bash
# Verifier for checkpoint: dep_chain
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
# Mentions all three canonical concept tokens — classpath is the discriminating
# one (dependency/bridge alone are already used generically in the prompt)
grep -qi 'dependency' "$REPORT" && grep -qi 'classpath' "$REPORT" && grep -qi 'bridge' "$REPORT" && FOUND=$((FOUND + 1))
# States the dependency relationship type
grep -qiE 'logging[_ -]?bridge' "$REPORT" && FOUND=$((FOUND + 1))

SCORE=$(awk "BEGIN {printf \"%.2f\", $FOUND/$TOTAL}")
PASSED=$([ "$FOUND" -ge "$TOTAL" ] && echo true || echo false)
printf '{"score": %s, "passed": %s, "detail": "Matched %d/%d patterns"}\n' "$SCORE" "$PASSED" "$FOUND" "$TOTAL"
