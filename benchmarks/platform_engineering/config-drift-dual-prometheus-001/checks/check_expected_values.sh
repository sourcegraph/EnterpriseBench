#!/usr/bin/env bash
# Checkpoint: agent determined the correct expected values per drift point.
# Criteria from expected_solution.json :: expected_values.
set -euo pipefail

REPORT="${WORKSPACE:-/workspace}/DRIFT_REPORT.json"
if [[ ! -f "$REPORT" ]]; then
  printf '{"score": 0.0, "passed": false, "reason": "DRIFT_REPORT.json not found"}\n'
  exit 0
fi

BLOB=$(jq -r '[.. | strings] | join(" ")' "$REPORT" 2>/dev/null || true)
FOUND=0
TOTAL=3

# min-block-duration == max-block-duration (== 2h) as the canonical compaction fix.
if printf '%s' "$BLOB" | grep -qiE 'block-duration|block_duration' && printf '%s' "$BLOB" | grep -qE '2h'; then
  FOUND=$((FOUND + 1))
fi
# external_labels (cluster/replica) required for Thanos deduplication.
if printf '%s' "$BLOB" | grep -qiE 'external.?label' && printf '%s' "$BLOB" | grep -qiE 'cluster|replica'; then
  FOUND=$((FOUND + 1))
fi
# Override chain: prometheus.yml/flags -> tsdb/db.go -> sidecar -> shipper.go.
if jq -e '.drift_points | any((.override_chain // []) | length > 0)' "$REPORT" >/dev/null 2>&1 \
   || printf '%s' "$BLOB" | grep -qiE 'override chain|prometheus\.yml|shipper\.go|sidecar'; then
  FOUND=$((FOUND + 1))
fi

SCORE=$(awk "BEGIN {printf \"%.2f\", $FOUND/$TOTAL}")
if [ "$FOUND" -ge 2 ]; then PASSED=true; else PASSED=false; fi
printf '{"score": %s, "passed": %s, "reason": "Correct expected values: %d/%d criteria"}\n' "$SCORE" "$PASSED" "$FOUND" "$TOTAL"
