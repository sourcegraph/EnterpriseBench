#!/usr/bin/env bash
# Checkpoint 1: Verify agent identified all repos requiring changes
set -euo pipefail

ANSWER="${WORKSPACE:-/workspace}/REFACTOR_PLAN.md"
if [[ ! -f "$ANSWER" ]]; then
  printf '{"score": 0.0, "passed": false, "reason": "REFACTOR_PLAN.md not found"}\n'
  exit 0
fi

FOUND=0
TOTAL=2
# Repo-specific artifacts only: the bare words "cobra"/"kubernetes" are already
# given in the prompt's context/environment sections, so each side additionally
# requires a go.mod anchor to prove the agent grounded the claim in the repos
# rather than echoing the task description.
if grep -qiE 'spf13/cobra' "$ANSWER" && grep -qiE 'go\.mod' "$ANSWER"; then FOUND=$((FOUND + 1)); fi
if grep -qiE 'kubernetes/kubernetes' "$ANSWER" && grep -qiE 'go\.mod' "$ANSWER"; then FOUND=$((FOUND + 1)); fi

SCORE=$(awk "BEGIN {printf \"%.2f\", $FOUND/$TOTAL}")
if [ "$FOUND" -ge "$TOTAL" ]; then PASSED=true; else PASSED=false; fi

printf '{"score": %s, "passed": %s, "reason": "Found %d/%d repos in plan"}\n' "$SCORE" "$PASSED" "$FOUND" "$TOTAL"
