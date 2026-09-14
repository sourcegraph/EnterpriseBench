#!/usr/bin/env bash
set -euo pipefail

REPORT="${WORKSPACE:-/workspace}/BLAST_RADIUS.md"
if [[ ! -f "$REPORT" ]]; then
  printf '{"score": 0.0, "passed": false, "reason": "BLAST_RADIUS.md not found"}\n'
  exit 0
fi

FOUND=0
TOTAL=3
V1_OK=0
V2_OK=0
# requests' own checked-out version, only found in requests/requests/__version__.py
if grep -qiE '2\.28\.0' "$REPORT"; then FOUND=$((FOUND + 1)); V1_OK=1; fi
# boto3's declared botocore pin range, only found in boto3/setup.py
if grep -qiE '1\.29\.0.*1\.30\.0|1\.29\.0' "$REPORT"; then FOUND=$((FOUND + 1)); V2_OK=1; fi
if grep -qiE 'affected|vulnerable|patched|needs.*upgrade' "$REPORT"; then FOUND=$((FOUND + 1)); fi

SCORE=$(awk "BEGIN {printf \"%.2f\", $FOUND/$TOTAL}")
if [ "$V1_OK" -eq 1 ] && [ "$V2_OK" -eq 1 ]; then PASSED=true; else PASSED=false; fi

printf '{"score": %s, "passed": %s, "reason": "Version analysis: %d/%d criteria met"}\n' "$SCORE" "$PASSED" "$FOUND" "$TOTAL"
