#!/usr/bin/env bash
# Checkpoint 1: Verify agent identified all repos requiring API migration
set -euo pipefail

ANSWER="${WORKSPACE:-/workspace}/REFACTOR_PLAN.md"
if [[ ! -f "$ANSWER" ]]; then
  printf '{"score": 0.0, "passed": false, "reason": "REFACTOR_PLAN.md not found"}\n'
  exit 0
fi

FOUND=0
TOTAL=3
# Repo-specific migration footprint only — bare repo names are given in the
# prompt's environment list, so each check below also requires evidence that
# could only come from investigating the repo (its actual call-site file,
# PR reference, or vendored dependency), not from the instructions alone.
if grep -qiE 'grpc-go|grpc/grpc-go' "$ANSWER"; then FOUND=$((FOUND + 1)); fi
if grep -qiE 'clientv3/client\.go|clientv3\.Client|etcd-io/etcd#?21282|DialTimeout' "$ANSWER"; then FOUND=$((FOUND + 1)); fi
if grep -qiE 'kubernetes/kubernetes#?128419|vendor.*(go\.)?etcd\.io|go\.mod.*etcd|etcd.*go\.mod|staging.*etcd' "$ANSWER"; then FOUND=$((FOUND + 1)); fi

SCORE=$(awk "BEGIN {printf \"%.2f\", $FOUND/$TOTAL}")
if [ "$FOUND" -ge "$TOTAL" ]; then PASSED=true; else PASSED=false; fi

printf '{"score": %s, "passed": %s, "reason": "Found %d/%d repos in plan"}\n' "$SCORE" "$PASSED" "$FOUND" "$TOTAL"
