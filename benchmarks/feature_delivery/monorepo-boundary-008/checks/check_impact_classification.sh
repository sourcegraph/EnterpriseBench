#!/usr/bin/env bash
# Checkpoint 2: Verify impact classification
set -euo pipefail

REPORT="${WORKSPACE:-/workspace}/next.js/IMPACT_REPORT.md"
if [[ ! -f "$REPORT" ]]; then
  printf '{"score": 0.0, "passed": false, "reason": "IMPACT_REPORT.md not found"}\n'
  exit 0
fi

CONTENT=$(cat "$REPORT" | tr '[:upper:]' '[:lower:]')
FOUND=0
TOTAL=2
# Criterion 1: overall semver classified as major.
if echo "$CONTENT" | grep -q 'major'; then FOUND=$((FOUND + 1)); fi
# Criterion 2: the major classification must be tied to the actual
# cross-language artifact (the SWC/next-core import-map crate), not just the
# generic semver word — that word alone is given in the prompt's output format.
if grep -qiE 'next_import_map|next-swc|next-core|packages/next-swc' "$REPORT"; then FOUND=$((FOUND + 1)); fi

SCORE=$(awk "BEGIN {printf \"%.2f\", $FOUND/$TOTAL}")
if [ "$FOUND" -ge "$TOTAL" ]; then PASSED=true; else PASSED=false; fi
printf '{"score": %s, "passed": %s, "reason": "Classification criteria %d/%d met"}\n' "$SCORE" "$PASSED" "$FOUND" "$TOTAL"
