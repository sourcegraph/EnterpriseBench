#!/usr/bin/env bash
# Checkpoint: drift report is well-formed and references both repos.
# Criteria from expected_solution.json :: config_valid.
set -euo pipefail

REPORT="${WORKSPACE:-/workspace}/DRIFT_REPORT.json"
if [[ ! -f "$REPORT" ]]; then
  printf '{"score": 0.0, "passed": false, "reason": "DRIFT_REPORT.json not found"}\n'
  exit 0
fi

if ! jq -e '(.drift_points | type) == "array" and (.drift_points | length) > 0' "$REPORT" >/dev/null 2>&1; then
  printf '{"score": 0.0, "passed": false, "reason": "Not valid JSON with a non-empty drift_points array"}\n'
  exit 0
fi

FOUND=1   # valid JSON + non-empty drift_points array
TOTAL=3

BLOB=$(jq -r '[.. | strings] | join(" ")' "$REPORT" 2>/dev/null || true)
# References both repos: a Prometheus TSDB source AND a Thanos sidecar/shipper source.
if printf '%s' "$BLOB" | grep -qiE 'tsdb/db\.go|prometheus' && printf '%s' "$BLOB" | grep -qiE 'sidecar\.go|shipper\.go|thanos'; then
  FOUND=$((FOUND + 1))
fi
# Each drift_point carries the required fields.
if jq -e '.drift_points | all(
    has("config_key") and has("prometheus_default") and has("prometheus_source_file")
    and has("thanos_expectation") and has("thanos_source_file") and has("failure_mode"))' "$REPORT" >/dev/null 2>&1; then
  FOUND=$((FOUND + 1))
fi

SCORE=$(awk "BEGIN {printf \"%.2f\", $FOUND/$TOTAL}")
if [ "$FOUND" -ge 2 ]; then PASSED=true; else PASSED=false; fi
printf '{"score": %s, "passed": %s, "reason": "Report validity: %d/%d criteria"}\n' "$SCORE" "$PASSED" "$FOUND" "$TOTAL"
