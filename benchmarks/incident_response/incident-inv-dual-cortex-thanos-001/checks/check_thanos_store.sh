#!/usr/bin/env bash
# Verifier for checkpoint: thanos_store
# Task: incident-inv-dual-cortex-thanos-001
set -euo pipefail

REPORT="${WORKSPACE:-/workspace}/analysis/IMPACT_REPORT.md"

if [[ ! -f "$REPORT" ]]; then
    printf '{"score": 0.0, "passed": false, "detail": "Report not found"}\n'
    exit 0
fi

FOUND=0
TOTAL=3

# Repo-specific artifacts only — generic incident terms (StoreAPI, discover,
# thanos) are given in the prompt and must not earn credit on their own.
grep -qiE 'pkg/store/proxy\.go|pkg/query/querier\.go' "$REPORT" && FOUND=$((FOUND + 1))
grep -qiE 'fan-out|fans out|fan out|gRPC.*StoreAPI|StoreAPI.*gRPC' "$REPORT" && FOUND=$((FOUND + 1))
grep -qiE 'duplicat.*series|inconsistent.*series|series.*duplicat|series.*inconsist' "$REPORT" && FOUND=$((FOUND + 1))

SCORE=$(awk "BEGIN {printf \"%.2f\", $FOUND/$TOTAL}")
PASSED=$([ "$FOUND" -ge 2 ] && echo true || echo false)
printf '{"score": %s, "passed": %s, "detail": "Matched %d/%d patterns"}\n' "$SCORE" "$PASSED" "$FOUND" "$TOTAL"
