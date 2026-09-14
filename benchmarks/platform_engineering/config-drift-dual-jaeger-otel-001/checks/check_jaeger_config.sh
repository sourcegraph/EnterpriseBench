#!/usr/bin/env bash
# Verifier for checkpoint: jaeger_config
# Task: config-drift-dual-jaeger-otel-001
set -euo pipefail

REPORT="${WORKSPACE:-/workspace}/analysis/IMPACT_REPORT.md"

if [[ ! -f "$REPORT" ]]; then
    printf '{"score": 0.0, "passed": false, "detail": "Report not found"}\n'
    exit 0
fi

FOUND=0
TOTAL=3

# Repo-specific artifacts only — generic words like "jaeger", "collector",
# "config" already appear in the prompt and must not earn credit.
grep -qiE 'cmd/collector/app/flags|flags/flags\.go|InitFromViper' "$REPORT" && FOUND=$((FOUND + 1))
grep -qiE 'CollectorOptions' "$REPORT" && FOUND=$((FOUND + 1))
grep -qiE -- '--collector\.(queue-size|num-workers|grpc-port|zipkin\.host-port|tags)|SPAN_STORAGE_TYPE' "$REPORT" && FOUND=$((FOUND + 1))

SCORE=$(awk "BEGIN {printf \"%.2f\", $FOUND/$TOTAL}")
PASSED=$([ "$FOUND" -ge 2 ] && echo true || echo false)
printf '{"score": %s, "passed": %s, "detail": "Matched %d/%d patterns"}\n' "$SCORE" "$PASSED" "$FOUND" "$TOTAL"
