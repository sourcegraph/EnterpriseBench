#!/usr/bin/env bash
set -euo pipefail

REPORT="${WORKSPACE:-/workspace}/BLAST_RADIUS.md"
if [[ ! -f "$REPORT" ]]; then
  printf '{"score": 0.0, "passed": false, "reason": "BLAST_RADIUS.md not found"}\n'
  exit 0
fi

FOUND=0
TOTAL=2
# Must cite the actual resolved/pinned version evidence found in the repos —
# urllib3's own checked-out version (src/urllib3/_version.py: 1.26.16) or
# requests' pin range on it (setup.cfg: >=1.21.1,<1.27) — not generic branch
# talk, which is no longer in the prompt.
if grep -qiE '1\.26\.16|1\.21\.1|<\s*1\.27\b' "$REPORT"; then FOUND=$((FOUND + 1)); fi
# Explicit affected/patched classification, paired implicitly with (1) above
# for full credit.
if grep -qiE 'vendor|pin|affected|vulnerable|patched|needs.*upgrade' "$REPORT"; then FOUND=$((FOUND + 1)); fi

SCORE=$(awk "BEGIN {printf \"%.2f\", $FOUND/$TOTAL}")
if [ "$FOUND" -ge 2 ]; then PASSED=true; else PASSED=false; fi

printf '{"score": %s, "passed": %s, "reason": "Version analysis: %d/%d criteria met"}\n' "$SCORE" "$PASSED" "$FOUND" "$TOTAL"
