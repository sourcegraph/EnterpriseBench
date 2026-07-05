#!/usr/bin/env bash
# Verifier for checkpoint: drift_analysis
# Task: config-drift-tri-javax-jakarta-spring-001
set -euo pipefail

REPORT="${WORKSPACE:-/workspace}/analysis/IMPACT_REPORT.md"

if [[ ! -f "$REPORT" ]]; then
    printf '{"score": 0.0, "passed": false, "detail": "Report not found"}\n'
    exit 0
fi

FOUND=0
TOTAL=3

grep -qiE 'ClassNotFoundException|NoClassDefFoundError|LinkageError' "$REPORT" && FOUND=$((FOUND + 1))
grep -qiE 'javax.*jakarta|mixed' "$REPORT" && FOUND=$((FOUND + 1))

ANCHORS=0
grep -qiE 'ServletWebServerApplicationContext' "$REPORT" && ANCHORS=$((ANCHORS + 1))
grep -qiE 'Configuration\.java|hibernate-core' "$REPORT" && ANCHORS=$((ANCHORS + 1))
grep -qiE 'Tomcat\.java|catalina' "$REPORT" && ANCHORS=$((ANCHORS + 1))
[ "$ANCHORS" -ge 2 ] && FOUND=$((FOUND + 1))

SCORE=$(awk "BEGIN {printf \"%.2f\", $FOUND/$TOTAL}")
PASSED=$([ "$FOUND" -ge "$TOTAL" ] && echo true || echo false)
printf '{"score": %s, "passed": %s, "detail": "Matched %d/%d patterns"}\n' "$SCORE" "$PASSED" "$FOUND" "$TOTAL"
