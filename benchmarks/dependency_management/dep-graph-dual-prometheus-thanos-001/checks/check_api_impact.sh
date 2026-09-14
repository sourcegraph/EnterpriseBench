#!/usr/bin/env bash
# Verifier for checkpoint: api_impact
# Task: dep-graph-dual-prometheus-thanos-001
set -euo pipefail

REPORT="${WORKSPACE:-/workspace}/analysis/IMPACT_REPORT.md"

if [[ ! -f "$REPORT" ]]; then
    printf '{"score": 0.0, "passed": false, "detail": "Report not found"}\n'
    exit 0
fi

FOUND=0
TOTAL=3

# 1. Must cite at least one canonical file spanning both repos — these
#    filenames are not in the prompt, so this forces the repo to be opened.
if grep -qiE '\bdb\.go\b|\bshipper\.go\b|\bcompact\.go\b|\bprometheus\.go\b' "$REPORT"; then
    FOUND=$((FOUND + 1))
fi

# 2. Must mention at least 3 of the 4 canonical concept tokens. "encode" and
#    "breaking" never appear verbatim in the prompt (the prompt only says
#    "chunk encoding" and never uses the word "breaking"), so a plain echo of
#    the prompt caps out at 2/4 here.
TOKENS=0
grep -qi 'compaction' "$REPORT" && TOKENS=$((TOKENS + 1))
grep -qi 'chunk' "$REPORT" && TOKENS=$((TOKENS + 1))
grep -qi 'encode' "$REPORT" && TOKENS=$((TOKENS + 1))
grep -qi 'breaking' "$REPORT" && TOKENS=$((TOKENS + 1))
[ "$TOKENS" -ge 3 ] && FOUND=$((FOUND + 1))

# 3. Must state the dependency relationship type.
if grep -qiE 'storage[ _-]?api' "$REPORT"; then
    FOUND=$((FOUND + 1))
fi

SCORE=$(awk "BEGIN {printf \"%.2f\", $FOUND/$TOTAL}")
PASSED=$([ "$FOUND" -ge "$TOTAL" ] && echo true || echo false)
printf '{"score": %s, "passed": %s, "detail": "Matched %d/%d patterns"}\n' "$SCORE" "$PASSED" "$FOUND" "$TOTAL"
