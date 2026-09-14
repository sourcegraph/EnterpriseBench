#!/usr/bin/env bash
# Checkpoint 2: Verify impact classification
set -euo pipefail

REPORT="${WORKSPACE:-/workspace}/babel/IMPACT_REPORT.md"
if [[ ! -f "$REPORT" ]]; then
  printf '{"score": 0.0, "passed": false, "reason": "IMPACT_REPORT.md not found"}\n'
  exit 0
fi

FOUND=0
TOTAL=3

# Flatten to a single line so a per-package verdict isn't missed purely
# because of markdown line-wrapping; proximity windows below still keep
# this from matching across unrelated sections of the document.
FLAT=$(tr '\n' ' ' < "$REPORT")

# 1. Overall semver classification must be stated somewhere.
if grep -qi 'minor' "$REPORT"; then FOUND=$((FOUND + 1)); fi

# 2. Per-package classification: @babel/parser's own "minor" bump must be
#    argued (package name and verdict co-located), not just both terms
#    appearing anywhere in the document independently.
if grep -qiE '@babel/parser.{0,120}minor|minor.{0,120}@babel/parser' <<< "$FLAT"; then
  FOUND=$((FOUND + 1))
fi

# 3. Per-package classification: @babel/plugin-proposal-decorators's own
#    "minor" bump must be argued the same way.
if grep -qiE 'plugin-proposal-decorators.{0,120}minor|minor.{0,120}plugin-proposal-decorators' <<< "$FLAT"; then
  FOUND=$((FOUND + 1))
fi

if [ "$TOTAL" -gt 0 ]; then
  SCORE=$(awk "BEGIN {printf \"%.2f\", $FOUND/$TOTAL}")
else
  SCORE="0.00"
fi
if [ "$FOUND" -ge 2 ]; then PASSED=true; else PASSED=false; fi

printf '{"score": %s, "passed": %s, "reason": "Found %d/%d classification signals"}\n' "$SCORE" "$PASSED" "$FOUND" "$TOTAL"
