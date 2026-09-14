#!/usr/bin/env bash
set -euo pipefail

REPORT="${WORKSPACE:-/workspace}/BLAST_RADIUS.md"
if [[ ! -f "$REPORT" ]]; then
  printf '{"score": 0.0, "passed": false, "reason": "BLAST_RADIUS.md not found"}\n'
  exit 0
fi

FOUND=0
TOTAL=2
# Linking strategy distinction, grounded in the actual build file that reveals it
if grep -qiE 'dynamic|static|link|pure.Go|CGO_ENABLED' "$REPORT" && \
   grep -qiE 'configure\.ac|CMakeLists\.txt|Makefile|go\.mod|hack/lib/golang\.sh' "$REPORT"; then
  FOUND=$((FOUND + 1))
fi
# Actual exposure classification tied to a specific consumer (not a bare adjective)
if grep -qiE '(curl|git|kubernetes).{0,40}(affected|vulnerable|exposure|immune|exposed)|(affected|vulnerable|exposure|immune|exposed).{0,40}(curl|git|kubernetes)' "$REPORT"; then
  FOUND=$((FOUND + 1))
fi

SCORE=$(awk "BEGIN {printf \"%.2f\", $FOUND/$TOTAL}")
if [ "$FOUND" -ge 2 ]; then PASSED=true; else PASSED=false; fi

printf '{"score": %s, "passed": %s, "reason": "Version/linking analysis: %d/%d criteria met"}\n' "$SCORE" "$PASSED" "$FOUND" "$TOTAL"
