#!/usr/bin/env bash
# Checkpoint 1: Verify agent identified all repos requiring changes
set -euo pipefail

ANSWER="${WORKSPACE:-/workspace}/REFACTOR_PLAN.md"
if [[ ! -f "$ANSWER" ]]; then
  printf '{"score": 0.0, "passed": false, "reason": "REFACTOR_PLAN.md not found"}\n'
  exit 0
fi

# Repo-specific evidence only — bare repo-name mentions are already given by
# the "Repos in Workspace" list in the prompt and earn no credit on their own.
# Each criterion requires a repo-specific artifact/symbol a correctly
# code-grounded answer would cite (per expected_solution.json's
# identify_repos criteria), none of which appear in instruction.md.
FOUND=0
TOTAL=3
# grpc-go: must name the actual breaking symbol found in its source (the
# ServiceRegistrar interface change), not just the repo name.
if grep -qiE 'grpc-go|grpc.go' "$ANSWER" && grep -qiE 'ServiceRegistrar' "$ANSWER"; then FOUND=$((FOUND + 1)); fi
# etcd: must cite its go.mod requiring the grpc-go bump.
if grep -qiE 'etcd' "$ANSWER" && grep -qiE 'go\.mod' "$ANSWER"; then FOUND=$((FOUND + 1)); fi
# kubernetes: must cite its go.mod requiring the grpc-go bump.
if grep -qiE 'kubernetes' "$ANSWER" && grep -qiE 'go\.mod' "$ANSWER"; then FOUND=$((FOUND + 1)); fi

SCORE=$(awk "BEGIN {printf \"%.2f\", $FOUND/$TOTAL}")
if [ "$FOUND" -ge "$TOTAL" ]; then PASSED=true; else PASSED=false; fi

printf '{"score": %s, "passed": %s, "reason": "Found %d/%d repo-specific artifacts"}\n' "$SCORE" "$PASSED" "$FOUND" "$TOTAL"
