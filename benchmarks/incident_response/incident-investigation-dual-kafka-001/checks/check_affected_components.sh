#!/usr/bin/env bash
# Checkpoint 3: Verify agent identified affected connector configurations
set -euo pipefail

export REPORT="${WORKSPACE:-/workspace}/kafka/INCIDENT_REPORT.md"
if [[ ! -f "$REPORT" ]]; then
  printf '{"score": 0.0, "passed": false, "reason": "INCIDENT_REPORT.md not found"}\n'
  exit 0
fi

FOUND=0
TOTAL=2

# Must name the Kafka Connect component that owns the put/flush/commit cycle
# (repo-specific class name, not the generic lifecycle description already in
# the prompt).
if grep -qiE 'WorkerSinkTask' "$REPORT"; then
  FOUND=$((FOUND + 1))
fi

# Must name the JDBC connector component(s) that own the transaction/batch
# insert (repo-specific class names, not the generic "batch.size" config
# already given in the prompt).
if grep -qiE 'JdbcSinkTask|BufferedRecords' "$REPORT"; then
  FOUND=$((FOUND + 1))
fi

if [ "$TOTAL" -gt 0 ]; then
  SCORE=$(awk "BEGIN {printf \"%.2f\", $FOUND/$TOTAL}")
else
  SCORE="0.00"
fi
if [ "$FOUND" -ge 1 ]; then
  PASSED=true
else
  PASSED=false
fi

printf '{"score": %s, "passed": %s, "reason": "Identified %d/%d affected configurations"}\n' "$SCORE" "$PASSED" "$FOUND" "$TOTAL"
