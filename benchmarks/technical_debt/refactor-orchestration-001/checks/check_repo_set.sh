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
# Bare "etcd"/"kubernetes" mentions are already given verbatim in the
# prompt's "Repos in Workspace" list, so pasting the prompt back would
# trivially satisfy a generic word match. Require the actual Go module
# identifiers a correct answer only knows from opening each repo's go.mod —
# etcd's module path is go.etcd.io/etcd (not the GitHub org/repo string),
# and kubernetes' top-level module is k8s.io/kubernetes — anchored to go.mod.
if grep -qiE 'go\.etcd\.io(/etcd)?|etcd[^.\n]*go\.mod|go\.mod[^.\n]*etcd' "$ANSWER"; then FOUND=$((FOUND + 1)); fi
if grep -qiE 'k8s\.io/kubernetes|kubernetes[^.\n]*go\.mod|go\.mod[^.\n]*kubernetes' "$ANSWER"; then FOUND=$((FOUND + 1)); fi

SCORE=$(awk "BEGIN {printf \"%.2f\", $FOUND/$TOTAL}")
if [ "$FOUND" -ge "$TOTAL" ]; then PASSED=true; else PASSED=false; fi

printf '{"score": %s, "passed": %s, "reason": "Found %d/%d repos in plan"}\n' "$SCORE" "$PASSED" "$FOUND" "$TOTAL"
