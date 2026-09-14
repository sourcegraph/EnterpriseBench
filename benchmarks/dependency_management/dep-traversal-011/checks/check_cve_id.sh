#!/usr/bin/env bash
set -euo pipefail

REPORT="${WORKSPACE:-/workspace}/BLAST_RADIUS.md"
if [[ ! -f "$REPORT" ]]; then
  printf '{"score": 0.0, "passed": false, "reason": "BLAST_RADIUS.md not found"}\n'
  exit 0
fi

FOUND=0
TOTAL=3
if grep -qi 'CVE-2021-44228' "$REPORT"; then FOUND=$((FOUND + 1)); fi
# Repo-specific: the full Maven coordinate or the actual module path (log4j-core
# is a submodule of logging-log4j2, not a top-level artifact) — not given in the prompt.
if grep -qiE 'org\.apache\.logging\.log4j:log4j-core|log4j-core/pom\.xml' "$REPORT"; then FOUND=$((FOUND + 1)); fi
# Repo-specific: the start of the vulnerable range, not stated in the prompt.
if grep -qiE '2\.0-beta9' "$REPORT"; then FOUND=$((FOUND + 1)); fi

SCORE=$(awk "BEGIN {printf \"%.2f\", $FOUND/$TOTAL}")
if [ "$FOUND" -ge 2 ]; then PASSED=true; else PASSED=false; fi

printf '{"score": %s, "passed": %s, "reason": "Identified %d/%d CVE details"}\n' "$SCORE" "$PASSED" "$FOUND" "$TOTAL"
