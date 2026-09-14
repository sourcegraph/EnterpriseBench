#!/usr/bin/env bash
set -euo pipefail

REPORT="${WORKSPACE:-/workspace}/BLAST_RADIUS.md"
if [[ ! -f "$REPORT" ]]; then
  printf '{"score": 0.0, "passed": false, "reason": "BLAST_RADIUS.md not found"}\n'
  exit 0
fi

FOUND=0
TOTAL=3
PIN_OK=0
VENDOR_OK=0
if grep -qiE 'botocore' "$REPORT"; then FOUND=$((FOUND + 1)); fi
# Repo-specific: the exact constraint boto3's own setup.py declares on botocore.
if grep -qiE 'botocore *(>=|~=) *1\.29\.0|botocore.*1\.29\.0.*1\.30\.0' "$REPORT"; then FOUND=$((FOUND + 1)); PIN_OK=1; fi
# Repo-specific: awscli does not pip-declare botocore/requests at all — it
# vendors them in-tree (awscli/botocore/, awscli/botocore/vendored/requests/).
if grep -qiE 'vendor|awscli/botocore|in-tree|bundl' "$REPORT"; then FOUND=$((FOUND + 1)); VENDOR_OK=1; fi

SCORE=$(awk "BEGIN {printf \"%.2f\", $FOUND/$TOTAL}")
if [ "$PIN_OK" -eq 1 ] && [ "$VENDOR_OK" -eq 1 ]; then PASSED=true; else PASSED=false; fi

printf '{"score": %s, "passed": %s, "reason": "Found %d/%d dependents"}\n' "$SCORE" "$PASSED" "$FOUND" "$TOTAL"
