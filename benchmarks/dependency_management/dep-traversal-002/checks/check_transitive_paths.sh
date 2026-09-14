#!/usr/bin/env bash
set -euo pipefail

REPORT="${WORKSPACE:-/workspace}/BLAST_RADIUS.md"
if [[ ! -f "$REPORT" ]]; then
  printf '{"score": 0.0, "passed": false, "reason": "BLAST_RADIUS.md not found"}\n'
  exit 0
fi

FOUND=0
TOTAL=2
# Repo-specific: the actual intermediary package name (grafana-plugin-sdk),
# not just "grafana" or "plugin" mentioned near each other.
if grep -qiE 'grafana[-_ ]?plugin[-_ ]?sdk' "$REPORT"; then FOUND=$((FOUND + 1)); fi
# Repo-specific: the actual intermediary package name (druid-web-console).
if grep -qiE 'druid[-_ ]?web[-_ ]?console' "$REPORT"; then FOUND=$((FOUND + 1)); fi

SCORE=$(awk "BEGIN {printf \"%.2f\", $FOUND/$TOTAL}")
if [ "$FOUND" -ge 2 ]; then PASSED=true; else PASSED=false; fi

printf '{"score": %s, "passed": %s, "reason": "Traced %d/%d transitive paths"}\n' "$SCORE" "$PASSED" "$FOUND" "$TOTAL"
