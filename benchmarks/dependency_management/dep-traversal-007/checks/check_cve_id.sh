#!/usr/bin/env bash
set -euo pipefail

REPORT="${WORKSPACE:-/workspace}/BLAST_RADIUS.md"
if [[ ! -f "$REPORT" ]]; then
  printf '{"score": 0.0, "passed": false, "reason": "BLAST_RADIUS.md not found"}\n'
  exit 0
fi

FOUND=0
TOTAL=2
if grep -qi 'CVE-2022-25878' "$REPORT"; then FOUND=$((FOUND + 1)); fi
# Repo-specific: the fixed release (not stated in the brief, only the
# vulnerable range is) or the actual manifest path inside the grpc-node
# monorepo / kubernetes-client-js repo that had to be located by exploring it.
if grep -qiE '6\.10\.3|6\.11\.3|packages/grpc-js|kubernetes-client-js' "$REPORT"; then FOUND=$((FOUND + 1)); fi

SCORE=$(awk "BEGIN {printf \"%.2f\", $FOUND/$TOTAL}")
if [ "$FOUND" -ge 2 ]; then PASSED=true; else PASSED=false; fi

printf '{"score": %s, "passed": %s, "reason": "Identified %d/%d CVE details"}\n' "$SCORE" "$PASSED" "$FOUND" "$TOTAL"
