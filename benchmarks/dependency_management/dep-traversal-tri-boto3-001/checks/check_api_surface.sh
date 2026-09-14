#!/usr/bin/env bash
# Checkpoint 1: Verify agent identified key classes and entry points in all three repos
set -euo pipefail

REPORT="${WORKSPACE:-/workspace}/DEPENDENCY_TRACE.md"
if [[ ! -f "$REPORT" ]]; then
  printf '{"score": 0.0, "passed": false, "reason": "DEPENDENCY_TRACE.md not found"}\n'
  exit 0
fi

FOUND=0
TOTAL=3
# Repo-specific artifacts only. "upload_file", "TransferManager", "s3transfer"
# and "botocore ... client" are already in the prompt (repo list / task
# framing), so a plain grep on those is satisfiable from instruction.md alone.
# Require the actual canonical basename or a symbol/option name that only
# shows up by reading the source (boto3/s3/transfer.py, s3transfer/manager.py
# or tasks.py, botocore/client.py or serialize.py; S3Transfer is matched
# case-sensitively so it can't be satisfied by the lowercase "s3transfer"
# package name that appears throughout the prompt).
if grep -qE 'transfer\.py|S3Transfer' "$REPORT" || grep -qiE 'S3\.resource|multipart_threshold' "$REPORT"; then FOUND=$((FOUND + 1)); fi
if grep -qE 'manager\.py|tasks\.py|MultipartUploader|UploadSubmissionTask' "$REPORT" || grep -qiE 'max_request_concurrency|max_submission_concurrency|multipart_chunksize' "$REPORT"; then FOUND=$((FOUND + 1)); fi
if grep -qE 'client\.py|serialize\.py' "$REPORT" || grep -qiE 'create_multipart_upload|upload_part|connect_timeout|read_timeout' "$REPORT"; then FOUND=$((FOUND + 1)); fi

SCORE=$(awk "BEGIN {printf \"%.2f\", $FOUND/$TOTAL}")
if [ "$FOUND" -ge "$TOTAL" ]; then PASSED=true; else PASSED=false; fi

printf '{"score": %s, "passed": %s, "reason": "Found %d/%d API surface areas across repos"}\n' "$SCORE" "$PASSED" "$FOUND" "$TOTAL"
