#!/usr/bin/env bash
set -euo pipefail

REPORT="${WORKSPACE:-/workspace}/BLAST_RADIUS.md"
if [[ ! -f "$REPORT" ]]; then
  printf '{"score": 0.0, "passed": false, "reason": "BLAST_RADIUS.md not found"}\n'
  exit 0
fi

FOUND=0
TOTAL=2
# Repo-specific artifact: the actual resolved golang.org/x/net version each
# repo pins (v0.10.0 at the pinned revs) — only discoverable by reading
# go.mod/go.sum, not by generic words like "version"/"resolved"/"pinned"
# which already appear in the prompt's own framing.
if grep -qiE '0\.10\.0' "$REPORT"; then FOUND=$((FOUND + 1)); fi
if grep -qiE 'affected|vulnerable|patched|not affected|unaffected|already fixed' "$REPORT"; then FOUND=$((FOUND + 1)); fi

SCORE=$(awk "BEGIN {printf \"%.2f\", $FOUND/$TOTAL}")
if [ "$FOUND" -ge 2 ]; then PASSED=true; else PASSED=false; fi

printf '{"score": %s, "passed": %s, "reason": "Version analysis: %d/%d criteria met"}\n' "$SCORE" "$PASSED" "$FOUND" "$TOTAL"
