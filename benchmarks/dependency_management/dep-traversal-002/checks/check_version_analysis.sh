#!/usr/bin/env bash
set -euo pipefail

REPORT="${WORKSPACE:-/workspace}/BLAST_RADIUS.md"
if [[ ! -f "$REPORT" ]]; then
  printf '{"score": 0.0, "passed": false, "reason": "BLAST_RADIUS.md not found"}\n'
  exit 0
fi

FOUND=0
TOTAL=3
# Specific version numbers (not just the generic word "vulnerable"/"affected").
if grep -qiE '0\.21\.2' "$REPORT"; then FOUND=$((FOUND + 1)); fi
# Must cite a lockfile as the source of the resolved/pinned version.
if grep -qiE 'yarn\.lock|package-lock\.json' "$REPORT"; then FOUND=$((FOUND + 1)); fi
# Must tie a named consumer to an explicit affected/not-affected verdict.
if grep -qiE '(grafana|druid).{0,60}(not.affected|unaffected|still.*vulnerable|needs.*upgrade|patched|safe|affected|vulnerable)' "$REPORT" \
   || grep -qiE '(not.affected|unaffected|still.*vulnerable|needs.*upgrade|patched|safe|affected|vulnerable).{0,60}(grafana|druid)' "$REPORT"; then
  FOUND=$((FOUND + 1))
fi

SCORE=$(awk "BEGIN {printf \"%.2f\", $FOUND/$TOTAL}")
if [ "$FOUND" -ge 2 ]; then PASSED=true; else PASSED=false; fi

printf '{"score": %s, "passed": %s, "reason": "Version analysis: %d/%d criteria met"}\n' "$SCORE" "$PASSED" "$FOUND" "$TOTAL"
