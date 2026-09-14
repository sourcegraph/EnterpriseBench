#!/usr/bin/env bash
# Checkpoint 2: Verify impact classification
set -euo pipefail

REPORT="${WORKSPACE:-/workspace}/babel/IMPACT_REPORT.md"
if [[ ! -f "$REPORT" ]]; then
  printf '{"score": 0.0, "passed": false, "reason": "IMPACT_REPORT.md not found"}\n'
  exit 0
fi

CONTENT=$(cat "$REPORT" | tr '[:upper:]' '[:lower:]')

FOUND=0
TOTAL=3
# (1) The semver word itself is generic and appears in no prompt text, but on
# its own could still be guessed — kept as a baseline requirement, not the
# sole gate.
if echo "$CONTENT" | grep -q 'patch'; then FOUND=$((FOUND + 1)); fi
# (2) preset-env's classification must be justified by the actual reason it
# is patch-level (only its regenerated fixtures changed, no API change) —
# ties the classification to the specific fixture evidence, not a guess.
if echo "$CONTENT" | grep -qE 'preset.env|@babel/preset-env' && echo "$CONTENT" | grep -qE 'for-of-array-block-scoping|no (public )?api|fixture'; then FOUND=$((FOUND + 1)); fi
# (3) The PR numbers that actually shipped these fixes are not in the
# prompt — citing one demonstrates the classification is grounded in the
# real change (e.g. PR file counts), not restated boilerplate.
if echo "$CONTENT" | grep -qE '#?16166|#?16011'; then FOUND=$((FOUND + 1)); fi

SCORE=$(awk "BEGIN {printf \"%.2f\", $FOUND/$TOTAL}")
if [ "$FOUND" -ge 2 ]; then PASSED=true; else PASSED=false; fi
printf '{"score": %s, "passed": %s, "reason": "Classification grounded in %d/%d repo-specific signals"}\n' "$SCORE" "$PASSED" "$FOUND" "$TOTAL"
