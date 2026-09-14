#!/usr/bin/env bash
# Checkpoint 2: Verify impact classification
set -euo pipefail

REPORT="${WORKSPACE:-/workspace}/pnpm/IMPACT_REPORT.md"
if [[ ! -f "$REPORT" ]]; then
  printf '{"score": 0.0, "passed": false, "reason": "IMPACT_REPORT.md not found"}\n'
  exit 0
fi

# Repo-specific classification evidence only. "major" and "v10.0.0" alone are
# already in the prompt (pnpm v10.0.0) and must not earn credit by themselves —
# each criterion below also requires reasoning tied to the actual code change,
# which is not stated in the prompt.
FOUND=0
TOTAL=3

# 1. Overall major bump gated on the v10.0.0 release, stated together.
if grep -qiE 'major.{0,40}v10\.0\.0|v10\.0\.0.{0,40}major' "$REPORT"; then FOUND=$((FOUND + 1)); fi

# 2. crypto.hash classified major with reasoning about the hash algorithm
#    itself changing (not just "hash format changes are breaking").
if grep -qiE 'crypto\.hash.{0,80}(algorithm|hash algorithm)|algorithm.{0,20}(shift|chang)' "$REPORT"; then FOUND=$((FOUND + 1)); fi

# 3. dependency-path classified major with reasoning about the *string output
#    format* changing for affected names (not just "path encoding failures").
if grep -qiE 'dependency-path.{0,80}(string|output) format|(string|output) format.{0,80}dependency-path' "$REPORT"; then FOUND=$((FOUND + 1)); fi

if [ "$TOTAL" -gt 0 ]; then
  SCORE=$(awk "BEGIN {printf \"%.2f\", $FOUND/$TOTAL}")
else
  SCORE="0.00"
fi
if [ "$FOUND" -ge 2 ]; then PASSED=true; else PASSED=false; fi

printf '{"score": %s, "passed": %s, "reason": "Found %d/%d classification criteria"}\n' "$SCORE" "$PASSED" "$FOUND" "$TOTAL"
