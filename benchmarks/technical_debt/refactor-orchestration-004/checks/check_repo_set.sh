#!/usr/bin/env bash
# Checkpoint 1: Verify agent identified all repos requiring changes
set -euo pipefail

ANSWER="${WORKSPACE:-/workspace}/REFACTOR_PLAN.md"
if [[ ! -f "$ANSWER" ]]; then
  printf '{"score": 0.0, "passed": false, "reason": "REFACTOR_PLAN.md not found"}\n'
  exit 0
fi

# Repo-specific artifacts only — the bare names "protobuf-go"/"grpc-go"/"etcd"
# and the bare module path "google.golang.org/protobuf" are already given in
# the prompt's workspace listing and must not earn credit on their own. Each
# grep instead requires the canonical GitHub org/repo identifier the agent
# can only produce by resolving each repo's real origin (go.mod module path,
# git remote, or README), matching the dependency_graph keys in
# ground_truth.json / expected_solution.json.
FOUND=0
TOTAL=3
if grep -qiE 'protocolbuffers/protobuf-go|protocolbuffers' "$ANSWER"; then FOUND=$((FOUND + 1)); fi
if grep -qiE 'grpc/grpc-go' "$ANSWER"; then FOUND=$((FOUND + 1)); fi
if grep -qiE 'etcd-io/etcd|etcd-io' "$ANSWER"; then FOUND=$((FOUND + 1)); fi

SCORE=$(awk "BEGIN {printf \"%.2f\", $FOUND/$TOTAL}")
if [ "$FOUND" -ge "$TOTAL" ]; then PASSED=true; else PASSED=false; fi

printf '{"score": %s, "passed": %s, "reason": "Found %d/%d repos in plan"}\n' "$SCORE" "$PASSED" "$FOUND" "$TOTAL"
