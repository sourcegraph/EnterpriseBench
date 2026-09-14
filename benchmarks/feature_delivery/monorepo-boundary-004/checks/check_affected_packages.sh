#!/usr/bin/env bash
# Checkpoint 1: Verify agent identified affected packages
set -euo pipefail

REPORT="${WORKSPACE:-/workspace}/babel/IMPACT_REPORT.md"
if [[ ! -f "$REPORT" ]]; then
  printf '{"score": 0.0, "passed": false, "reason": "IMPACT_REPORT.md not found"}\n'
  exit 0
fi

FOUND=0
TOTAL=3
# (1) The source for-of plugin is named in the prompt as one of the two
# already-fixed packages, so this alone is not repo-specific evidence.
if grep -qiE 'plugin.transform.for.of|@babel/plugin-transform-for-of' "$REPORT"; then FOUND=$((FOUND + 1)); fi
# (2) preset-env must be tied to the *specific* regenerated integration
# fixture directory, not just the package name — proves the agent actually
# traced the fixture tree rather than guessing "preset-env bundles everything".
if grep -qiE 'preset.env|@babel/preset-env' "$REPORT" && grep -qiE 'for-of-array-block-scoping' "$REPORT"; then FOUND=$((FOUND + 1)); fi
# (3) The PR numbers that shipped these fixes are not stated anywhere in the
# prompt — citing them requires actually finding the commits/changelog.
if grep -qE '#?16166|#?16011' "$REPORT"; then FOUND=$((FOUND + 1)); fi

# Compute score as proper decimal
if [ "$TOTAL" -gt 0 ]; then
  SCORE=$(awk "BEGIN {printf \"%.2f\", $FOUND/$TOTAL}")
else
  SCORE="0.00"
fi
if [ "$FOUND" -ge 2 ]; then PASSED=true; else PASSED=false; fi

printf '{"score": %s, "passed": %s, "reason": "Found %d/%d affected packages"}\n' "$SCORE" "$PASSED" "$FOUND" "$TOTAL"
