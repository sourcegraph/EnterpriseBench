#!/usr/bin/env bash
set -euo pipefail

REPORT="${WORKSPACE:-/workspace}/BLAST_RADIUS.md"
if [[ ! -f "$REPORT" ]]; then
  printf '{"score": 0.0, "passed": false, "reason": "BLAST_RADIUS.md not found"}\n'
  exit 0
fi

FOUND=0
TOTAL=2
# Repo-specific: grafana as a direct dependent must be tied to the manifest
# file that declares axios (its root package.json), not just the repo name.
if grep -qiE 'grafana' "$REPORT" && grep -qiE 'package\.json' "$REPORT"; then FOUND=$((FOUND + 1)); fi
# Repo-specific: druid's dependency runs through its web-console subfolder
# manifest — require that exact path, not the generic word "druid".
if grep -qiE 'web-console/package\.json|web.console.*package\.json|package\.json.*web.console' "$REPORT"; then FOUND=$((FOUND + 1)); fi

SCORE=$(awk "BEGIN {printf \"%.2f\", $FOUND/$TOTAL}")
if [ "$FOUND" -ge 2 ]; then PASSED=true; else PASSED=false; fi

printf '{"score": %s, "passed": %s, "reason": "Found %d/%d direct dependents"}\n' "$SCORE" "$PASSED" "$FOUND" "$TOTAL"
