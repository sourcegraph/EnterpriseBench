#!/usr/bin/env bash
set -euo pipefail

REPORT="${WORKSPACE:-/workspace}/BLAST_RADIUS.md"
if [[ ! -f "$REPORT" ]]; then
  printf '{"score": 0.0, "passed": false, "reason": "BLAST_RADIUS.md not found"}\n'
  exit 0
fi

FOUND=0
TOTAL=2
if grep -qiE 'requests' "$REPORT"; then FOUND=$((FOUND + 1)); fi
# Must cite the actual pinned constraint found in requests' own manifest
# (setup.cfg: urllib3>=1.21.1,<1.27) — not stated anywhere in the prompt.
if grep -qiE '1\.21\.1|<\s*1\.27\b' "$REPORT"; then FOUND=$((FOUND + 1)); fi

SCORE=$(awk "BEGIN {printf \"%.2f\", $FOUND/$TOTAL}")
if [ "$FOUND" -ge 2 ]; then PASSED=true; else PASSED=false; fi

printf '{"score": %s, "passed": %s, "reason": "Found %d/%d dependents"}\n' "$SCORE" "$PASSED" "$FOUND" "$TOTAL"
