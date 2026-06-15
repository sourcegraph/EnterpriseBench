#!/usr/bin/env bash
# Checkpoint: agent identified the Prometheus-vs-Thanos drift points.
# Criteria from expected_solution.json :: drift_points.
set -euo pipefail

REPORT="${WORKSPACE:-/workspace}/DRIFT_REPORT.json"
if [[ ! -f "$REPORT" ]]; then
  printf '{"score": 0.0, "passed": false, "reason": "DRIFT_REPORT.json not found"}\n'
  exit 0
fi

BLOB=$(jq -r '[.. | strings] | join(" ")' "$REPORT" 2>/dev/null || true)
FOUND=0
TOTAL=4

# Prometheus TSDB defaults source.
if printf '%s' "$BLOB" | grep -qE 'tsdb/db\.go'; then FOUND=$((FOUND + 1)); fi
# Thanos sidecar validation / shipper upload sources.
if printf '%s' "$BLOB" | grep -qE 'cmd/thanos/sidecar\.go|sidecar\.go|pkg/shipper/shipper\.go|shipper\.go'; then FOUND=$((FOUND + 1)); fi
# At least three drift points with concrete config keys.
if jq -e '(.drift_points | length) >= 3' "$REPORT" >/dev/null 2>&1 \
   && printf '%s' "$BLOB" | grep -qiE 'block-duration|min-block|max-block|retention|wal|external.?label'; then
  FOUND=$((FOUND + 1))
fi
# Failure mode for each drift explained.
if printf '%s' "$BLOB" | grep -qiE 'compaction|block not found|missing.?block|upload|dedup|refus'; then FOUND=$((FOUND + 1)); fi

SCORE=$(awk "BEGIN {printf \"%.2f\", $FOUND/$TOTAL}")
if [ "$FOUND" -ge 2 ]; then PASSED=true; else PASSED=false; fi
printf '{"score": %s, "passed": %s, "reason": "Found %d/%d drift-point criteria"}\n' "$SCORE" "$PASSED" "$FOUND" "$TOTAL"
