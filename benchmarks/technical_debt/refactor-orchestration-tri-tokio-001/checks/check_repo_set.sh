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
# Repo-specific artifacts only -- bare "tokio"/"hyper"/"axum" are already given
# as workspace directory names in instruction.md, so they don't discriminate a
# closed-book echo from a grounded answer. Require either the canonical
# GitHub org/repo identifier (as found in each crate's own Cargo.toml
# `repository = "..."` field) or the GT required-file anchor.
if grep -qiE 'tokio-rs/tokio|tokio/Cargo\.toml' "$ANSWER"; then FOUND=$((FOUND + 1)); fi
if grep -qiE 'hyperium/hyper' "$ANSWER"; then FOUND=$((FOUND + 1)); fi
if grep -qiE 'tokio-rs/axum|axum/Cargo\.toml' "$ANSWER"; then FOUND=$((FOUND + 1)); fi

SCORE=$(awk "BEGIN {printf \"%.2f\", $FOUND/$TOTAL}")
if [ "$FOUND" -ge "$TOTAL" ]; then PASSED=true; else PASSED=false; fi

printf '{"score": %s, "passed": %s, "reason": "Found %d/%d repos in plan"}\n' "$SCORE" "$PASSED" "$FOUND" "$TOTAL"
