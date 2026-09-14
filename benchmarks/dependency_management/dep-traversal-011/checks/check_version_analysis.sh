#!/usr/bin/env bash
set -euo pipefail

REPORT="${WORKSPACE:-/workspace}/BLAST_RADIUS.md"
if [[ ! -f "$REPORT" ]]; then
  printf '{"score": 0.0, "passed": false, "reason": "BLAST_RADIUS.md not found"}\n'
  exit 0
fi

FOUND=0
TOTAL=3
# Repo-specific: the actual fix version numbers — the prompt describes the fix
# staging in prose but never states these digits.
if grep -qiE '2\.15\.0|2\.16\.0' "$REPORT"; then FOUND=$((FOUND + 1)); fi
# Repo-specific: the actual backport version numbers — same reasoning.
if grep -qiE '2\.3\.1|2\.12\.2' "$REPORT"; then FOUND=$((FOUND + 1)); fi
# Consumer classification
if grep -qiE 'affected|vulnerable|patched|needs.*upgrade' "$REPORT"; then FOUND=$((FOUND + 1)); fi

SCORE=$(awk "BEGIN {printf \"%.2f\", $FOUND/$TOTAL}")
if [ "$FOUND" -ge 2 ]; then PASSED=true; else PASSED=false; fi

printf '{"score": %s, "passed": %s, "reason": "Version analysis: %d/%d criteria met"}\n' "$SCORE" "$PASSED" "$FOUND" "$TOTAL"
