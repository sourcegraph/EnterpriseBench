#!/usr/bin/env bash
set -euo pipefail

REPORT="${WORKSPACE:-/workspace}/BLAST_RADIUS.md"
if [[ ! -f "$REPORT" ]]; then
  printf '{"score": 0.0, "passed": false, "reason": "BLAST_RADIUS.md not found"}\n'
  exit 0
fi

FOUND=0
TOTAL=3
# Repo-specific artifacts only — naming a workspace repo alone is not enough
# (the prompt's Environment section already lists all three). Each dependent
# must be tied to the manifest evidence a real go.mod/go.sum read would give.
if grep -qiE 'grpc-go.*go\.(mod|sum)|go\.(mod|sum).*grpc-go' "$REPORT"; then FOUND=$((FOUND + 1)); fi
if grep -qiE 'istio.*go\.(mod|sum)|go\.(mod|sum).*istio' "$REPORT"; then FOUND=$((FOUND + 1)); fi
if grep -qiE '(go-control-plane|control-plane).*go\.(mod|sum)|go\.(mod|sum).*(go-control-plane|control-plane)' "$REPORT"; then FOUND=$((FOUND + 1)); fi

SCORE=$(awk "BEGIN {printf \"%.2f\", $FOUND/$TOTAL}")
if [ "$FOUND" -ge 2 ]; then PASSED=true; else PASSED=false; fi

printf '{"score": %s, "passed": %s, "reason": "Found %d/%d direct dependents"}\n' "$SCORE" "$PASSED" "$FOUND" "$TOTAL"
