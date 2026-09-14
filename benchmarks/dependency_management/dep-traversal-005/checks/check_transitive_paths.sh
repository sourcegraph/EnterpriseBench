#!/usr/bin/env bash
set -euo pipefail

REPORT="${WORKSPACE:-/workspace}/BLAST_RADIUS.md"
if [[ ! -f "$REPORT" ]]; then
  printf '{"score": 0.0, "passed": false, "reason": "BLAST_RADIUS.md not found"}\n'
  exit 0
fi

FOUND=0
TOTAL=3
# Repo-specific artifact: an explicit path/chain (arrow notation) from the
# affected module into each repo, not just both terms appearing somewhere in
# the report (that's satisfied by the prompt's own scenario framing).
if grep -qiE 'x/net[^\n]*->[^\n]*prometheus|prometheus[^\n]*->[^\n]*x/net' "$REPORT"; then FOUND=$((FOUND + 1)); fi
if grep -qiE 'x/net[^\n]*->[^\n]*consul|consul[^\n]*->[^\n]*x/net' "$REPORT"; then FOUND=$((FOUND + 1)); fi
if grep -qiE 'x/net[^\n]*->[^\n]*vault|vault[^\n]*->[^\n]*x/net' "$REPORT"; then FOUND=$((FOUND + 1)); fi

SCORE=$(awk "BEGIN {printf \"%.2f\", $FOUND/$TOTAL}")
if [ "$FOUND" -ge 2 ]; then PASSED=true; else PASSED=false; fi

printf '{"score": %s, "passed": %s, "reason": "Traced %d/%d dependency paths"}\n' "$SCORE" "$PASSED" "$FOUND" "$TOTAL"
