#!/usr/bin/env bash
# Verifier for checkpoint: prometheus_tsdb
# Task: dep-graph-dual-prometheus-thanos-001
set -euo pipefail

REPORT="${WORKSPACE:-/workspace}/analysis/IMPACT_REPORT.md"

if [[ ! -f "$REPORT" ]]; then
    printf '{"score": 0.0, "passed": false, "detail": "Report not found"}\n'
    exit 0
fi

FOUND=0
TOTAL=3

# 1. Must cite the canonical Prometheus TSDB file(s) by basename/path — these
#    filenames are not in the prompt, so this forces the repo to be opened.
if grep -qiE '\btsdb/db\.go\b|\btsdb/compact\.go\b|\bdb\.go\b|\bcompact\.go\b' "$REPORT"; then
    FOUND=$((FOUND + 1))
fi

# 2. Must mention at least 3 of the 4 canonical concept tokens. "Compactor"
#    and "Block" are required case-sensitive (the Go symbol names) — the
#    prompt only ever uses the lowercase generic words "compaction"/"block",
#    which do not satisfy these two.
TOKENS=0
grep -qi 'tsdb' "$REPORT" && TOKENS=$((TOKENS + 1))
grep -qi 'storage' "$REPORT" && TOKENS=$((TOKENS + 1))
grep -q 'Compactor' "$REPORT" && TOKENS=$((TOKENS + 1))
grep -q 'Block' "$REPORT" && TOKENS=$((TOKENS + 1))
[ "$TOKENS" -ge 3 ] && FOUND=$((FOUND + 1))

# 3. Must state the dependency relationship type.
if grep -qiE 'storage[ _-]?api' "$REPORT"; then
    FOUND=$((FOUND + 1))
fi

SCORE=$(awk "BEGIN {printf \"%.2f\", $FOUND/$TOTAL}")
PASSED=$([ "$FOUND" -ge "$TOTAL" ] && echo true || echo false)
printf '{"score": %s, "passed": %s, "detail": "Matched %d/%d patterns"}\n' "$SCORE" "$PASSED" "$FOUND" "$TOTAL"
