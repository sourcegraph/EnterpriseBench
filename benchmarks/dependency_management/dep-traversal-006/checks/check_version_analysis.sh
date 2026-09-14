#!/usr/bin/env bash
set -euo pipefail

REPORT="${WORKSPACE:-/workspace}/BLAST_RADIUS.md"
if [[ ! -f "$REPORT" ]]; then
  printf '{"score": 0.0, "passed": false, "reason": "BLAST_RADIUS.md not found"}\n'
  exit 0
fi

FOUND=0
TOTAL=2
# Repo-specific artifacts only — a concrete resolved version number, not just
# the generic words "version"/"go.mod" that already appear in the prompt.
if grep -qiE 'v1\.3[0-9]\.[0-9]+' "$REPORT"; then FOUND=$((FOUND + 1)); fi
# and an explicit affected/not-affected call tied to the 1.33.0 boundary.
if grep -qiE '(affected|vulnerable|not affected|unaffected|patched).*1\.33\.0|1\.33\.0.*(affected|vulnerable|not affected|unaffected|patched)' "$REPORT"; then FOUND=$((FOUND + 1)); fi

SCORE=$(awk "BEGIN {printf \"%.2f\", $FOUND/$TOTAL}")
if [ "$FOUND" -ge 2 ]; then PASSED=true; else PASSED=false; fi

printf '{"score": %s, "passed": %s, "reason": "Version analysis: %d/%d criteria met"}\n' "$SCORE" "$PASSED" "$FOUND" "$TOTAL"
