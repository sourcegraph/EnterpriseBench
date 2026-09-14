#!/usr/bin/env bash
# Checkpoint 1: Verify agent identified all repos requiring changes
set -euo pipefail

ANSWER="${WORKSPACE:-/workspace}/REFACTOR_PLAN.md"
if [[ ! -f "$ANSWER" ]]; then
  printf '{"score": 0.0, "passed": false, "reason": "REFACTOR_PLAN.md not found"}\n'
  exit 0
fi

FOUND=0
TOTAL=3
# Naming a repo isn't enough — the prompt already lists all three in the
# workspace. Each point requires the repo name PLUS a repo-specific artifact
# (a package/path/subsystem) only found by actually reading that repo.
if grep -qiE 'babel' "$ANSWER" && grep -qiE '@babel/parser|packages/babel-parser|@babel/traverse|@babel/types' "$ANSWER"; then FOUND=$((FOUND + 1)); fi
if grep -qiE 'webpack' "$ANSWER" && grep -qiE 'babel-loader|tree-shaking|tree shaking' "$ANSWER"; then FOUND=$((FOUND + 1)); fi
if grep -qiE 'next\.js|nextjs|next js' "$ANSWER" && grep -qiE 'packages/next|next/font|next/image|swc' "$ANSWER"; then FOUND=$((FOUND + 1)); fi

SCORE=$(awk "BEGIN {printf \"%.2f\", $FOUND/$TOTAL}")
if [ "$FOUND" -ge "$TOTAL" ]; then PASSED=true; else PASSED=false; fi

printf '{"score": %s, "passed": %s, "reason": "Found %d/%d repos with grounded artifacts in plan"}\n' "$SCORE" "$PASSED" "$FOUND" "$TOTAL"
