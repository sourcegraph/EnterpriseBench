#!/usr/bin/env bash
# Checkpoint 2: Verify agent traced the error chain from batch to task failure
set -euo pipefail

export REPORT="${WORKSPACE:-/workspace}/kafka/INCIDENT_REPORT.md"
if [[ ! -f "$REPORT" ]]; then
  printf '{"score": 0.0, "passed": false, "reason": "INCIDENT_REPORT.md not found"}\n'
  exit 0
fi

FOUND=0
TOTAL=4

# Hop 1: WorkerSinkTask polling/handing off the large batch (repo-specific
# class name, not the generic "large batch" phrasing already in the prompt).
if grep -qiE 'WorkerSinkTask' "$REPORT" && grep -qiE 'poll|batch' "$REPORT"; then
  FOUND=$((FOUND + 1))
fi

# Hop 2/3: JdbcSinkTask/BufferedRecords owning the transaction and batch
# insert (repo-specific class names, not the generic "JDBC transaction"
# phrasing already in the prompt).
if grep -qiE 'JdbcSinkTask|BufferedRecords' "$REPORT"; then
  FOUND=$((FOUND + 1))
fi

# Hop 4/5: the DB-side timeout actually rolling back the transaction — must
# name the rollback mechanism, not just echo "statement_timeout"/"SQLException"
# from the alert text.
if grep -qiE 'statement.*timeout|SQLException' "$REPORT" && grep -qiE 'rollback|rolls? back|rolled back' "$REPORT"; then
  FOUND=$((FOUND + 1))
fi

# Hop 6/7/8: WorkerSinkTask marking the task FAILED and the restart loop that
# follows (repo-specific class name tied to the failure, not just the
# "restart"/"FAILED" symptom wording already in the prompt).
if grep -qiE 'WorkerSinkTask' "$REPORT" && grep -qiE 'FAILED|restart' "$REPORT"; then
  FOUND=$((FOUND + 1))
fi

if [ "$TOTAL" -gt 0 ]; then
  SCORE=$(awk "BEGIN {printf \"%.2f\", $FOUND/$TOTAL}")
else
  SCORE="0.00"
fi
if [ "$FOUND" -ge 2 ]; then
  PASSED=true
else
  PASSED=false
fi

printf '{"score": %s, "passed": %s, "reason": "Traced %d/%d error chain steps"}\n' "$SCORE" "$PASSED" "$FOUND" "$TOTAL"
