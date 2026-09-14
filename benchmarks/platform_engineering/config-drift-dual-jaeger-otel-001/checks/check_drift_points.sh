#!/usr/bin/env bash
# Verifier for checkpoint: drift_points
# Task: config-drift-dual-jaeger-otel-001
set -euo pipefail

REPORT="${WORKSPACE:-/workspace}/analysis/IMPACT_REPORT.md"

if [[ ! -f "$REPORT" ]]; then
    printf '{"score": 0.0, "passed": false, "detail": "Report not found"}\n'
    exit 0
fi

FOUND=0
TOTAL=3

# Repo-specific artifacts only — generic words like "drift", "migration",
# "config", "parameter" already appear in the prompt and must not earn credit.
# Hop 1: the format drift itself — must cite BOTH sides' config-source files.
if grep -qiE 'cmd/collector/app/flags|flags/flags\.go' "$REPORT" && grep -qiE 'service/service\.go' "$REPORT"; then
    FOUND=$((FOUND + 1))
fi
# Hop 2: at least one concrete drifting config key.
grep -qiE -- '--collector\.(queue-size|num-workers|grpc-port|zipkin\.host-port|tags)|SPAN_STORAGE_TYPE' "$REPORT" && FOUND=$((FOUND + 1))
# Hop 3: the OTel-side mapping target for the drift.
grep -qiE 'receiver/otlpreceiver|otlpreceiver/factory\.go' "$REPORT" && FOUND=$((FOUND + 1))

SCORE=$(awk "BEGIN {printf \"%.2f\", $FOUND/$TOTAL}")
PASSED=$([ "$FOUND" -ge 2 ] && echo true || echo false)
printf '{"score": %s, "passed": %s, "detail": "Matched %d/%d patterns"}\n' "$SCORE" "$PASSED" "$FOUND" "$TOTAL"
