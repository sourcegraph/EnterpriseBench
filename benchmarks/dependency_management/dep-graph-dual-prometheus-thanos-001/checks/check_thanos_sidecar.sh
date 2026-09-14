#!/usr/bin/env bash
# Verifier for checkpoint: thanos_sidecar
# Task: dep-graph-dual-prometheus-thanos-001
set -euo pipefail

REPORT="${WORKSPACE:-/workspace}/analysis/IMPACT_REPORT.md"

if [[ ! -f "$REPORT" ]]; then
    printf '{"score": 0.0, "passed": false, "detail": "Report not found"}\n'
    exit 0
fi

FOUND=0
TOTAL=3

# 1. Must cite the canonical Thanos sidecar file(s) by basename/path — these
#    filenames are not in the prompt, so this forces the repo to be opened.
if grep -qiE 'pkg/shipper/shipper\.go|pkg/store/prometheus\.go|\bshipper\.go\b|\bprometheus\.go\b' "$REPORT"; then
    FOUND=$((FOUND + 1))
fi

# 2. Must mention all 3 canonical concept tokens. "shipper" is the
#    discriminating one — it never appears in the prompt, only in the actual
#    Thanos code (pkg/shipper), so this alone forces grounding.
TOKENS=0
grep -qi 'sidecar' "$REPORT" && TOKENS=$((TOKENS + 1))
grep -qi 'shipper' "$REPORT" && TOKENS=$((TOKENS + 1))
grep -qi 'block' "$REPORT" && TOKENS=$((TOKENS + 1))
[ "$TOKENS" -ge 3 ] && FOUND=$((FOUND + 1))

# 3. Must state the dependency relationship type.
if grep -qiE 'storage[ _-]?api' "$REPORT"; then
    FOUND=$((FOUND + 1))
fi

SCORE=$(awk "BEGIN {printf \"%.2f\", $FOUND/$TOTAL}")
PASSED=$([ "$FOUND" -ge "$TOTAL" ] && echo true || echo false)
printf '{"score": %s, "passed": %s, "detail": "Matched %d/%d patterns"}\n' "$SCORE" "$PASSED" "$FOUND" "$TOTAL"
