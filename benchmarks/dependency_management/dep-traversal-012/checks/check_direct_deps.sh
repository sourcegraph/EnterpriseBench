#!/usr/bin/env bash
set -euo pipefail

REPORT="${WORKSPACE:-/workspace}/BLAST_RADIUS.md"
if [[ ! -f "$REPORT" ]]; then
  printf '{"score": 0.0, "passed": false, "reason": "BLAST_RADIUS.md not found"}\n'
  exit 0
fi

FOUND=0
TOTAL=3
# curl's own build config must be cited as the evidence for its OpenSSL dependency
if grep -qiE 'curl' "$REPORT" && grep -qiE 'configure\.ac|CMakeLists\.txt' "$REPORT"; then FOUND=$((FOUND + 1)); fi
# git's own build config must be cited as the evidence for its curl dependency
if grep -qiE '\bgit\b' "$REPORT" && grep -qiE 'Makefile' "$REPORT"; then FOUND=$((FOUND + 1)); fi
# kubernetes's own module/build config must be cited as the evidence for its crypto/tls path
if grep -qiE 'kubernetes' "$REPORT" && grep -qiE 'go\.mod|hack/lib/golang\.sh' "$REPORT"; then FOUND=$((FOUND + 1)); fi

SCORE=$(awk "BEGIN {printf \"%.2f\", $FOUND/$TOTAL}")
if [ "$FOUND" -ge 2 ]; then PASSED=true; else PASSED=false; fi

printf '{"score": %s, "passed": %s, "reason": "Found %d/%d cross-ecosystem consumers"}\n' "$SCORE" "$PASSED" "$FOUND" "$TOTAL"
