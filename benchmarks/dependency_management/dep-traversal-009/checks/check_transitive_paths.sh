#!/usr/bin/env bash
set -euo pipefail

REPORT="${WORKSPACE:-/workspace}/BLAST_RADIUS.md"
if [[ ! -f "$REPORT" ]]; then
  printf '{"score": 0.0, "passed": false, "reason": "BLAST_RADIUS.md not found"}\n'
  exit 0
fi

FOUND=0
TOTAL=3
# Hop 1 (urllib3 -> requests): the exact pin range from requests/setup.cfg —
# generic mentions of the package names are not enough (they're in the prompt's
# environment list); the constraint numbers are not.
if grep -qiE '1\.21\.1|<\s*1\.27\b' "$REPORT"; then FOUND=$((FOUND + 1)); fi
# Hop 2 (requests -> botocore -> boto3): botocore isn't checked out as its own
# repo, so this can only come from reading boto3's own manifest, which pins
# botocore>=1.29.0,<1.30.0.
if grep -qi 'botocore' "$REPORT" && grep -qiE '1\.29\.0|1\.30\.0' "$REPORT"; then FOUND=$((FOUND + 1)); fi
# Full ordered chain, all four names within one statement.
if grep -qiE 'urllib3.{0,80}requests.{0,80}botocore.{0,80}boto3' "$REPORT"; then FOUND=$((FOUND + 1)); fi

SCORE=$(awk "BEGIN {printf \"%.2f\", $FOUND/$TOTAL}")
if [ "$FOUND" -ge 2 ]; then PASSED=true; else PASSED=false; fi

printf '{"score": %s, "passed": %s, "reason": "Traced %d/%d transitive paths"}\n' "$SCORE" "$PASSED" "$FOUND" "$TOTAL"
