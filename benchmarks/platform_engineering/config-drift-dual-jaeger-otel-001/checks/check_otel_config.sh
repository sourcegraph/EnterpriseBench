#!/usr/bin/env bash
# Verifier for checkpoint: otel_config
# Task: config-drift-dual-jaeger-otel-001
set -euo pipefail

REPORT="${WORKSPACE:-/workspace}/analysis/IMPACT_REPORT.md"

if [[ ! -f "$REPORT" ]]; then
    printf '{"score": 0.0, "passed": false, "detail": "Report not found"}\n'
    exit 0
fi

FOUND=0
TOTAL=3

# Repo-specific artifacts only — generic words like "otel", "opentelemetry",
# "config" already appear in the prompt and must not earn credit.
grep -qiE 'service/service\.go' "$REPORT" && FOUND=$((FOUND + 1))
grep -qiE 'receiver/otlpreceiver|otlpreceiver/factory\.go' "$REPORT" && FOUND=$((FOUND + 1))
grep -qiE 'service\.pipelines\.traces|receivers\.otlp|exporters\.otlp|processors\.batch' "$REPORT" && FOUND=$((FOUND + 1))

SCORE=$(awk "BEGIN {printf \"%.2f\", $FOUND/$TOTAL}")
PASSED=$([ "$FOUND" -ge 2 ] && echo true || echo false)
printf '{"score": %s, "passed": %s, "detail": "Matched %d/%d patterns"}\n' "$SCORE" "$PASSED" "$FOUND" "$TOTAL"
