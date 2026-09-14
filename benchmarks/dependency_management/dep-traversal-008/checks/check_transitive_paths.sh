#!/usr/bin/env bash
set -euo pipefail

REPORT="${WORKSPACE:-/workspace}/BLAST_RADIUS.md"
if [[ ! -f "$REPORT" ]]; then
  printf '{"score": 0.0, "passed": false, "reason": "BLAST_RADIUS.md not found"}\n'
  exit 0
fi

FOUND=0
TOTAL=3
HOP1_OK=0
HOP2_OK=0
# hop 1: boto3 -> botocore, evidenced by the exact pin in boto3/setup.py
# (never stated in the prompt) rather than the generic pair of package names.
if grep -qiE 'botocore *(>=|~=) *1\.29\.0|boto3.*setup\.py.*botocore|setup\.py.*botocore.*1\.29' "$REPORT"; then FOUND=$((FOUND + 1)); HOP1_OK=1; fi
# hop 2: botocore -> requests is only reachable in this workspace through
# awscli's in-tree vendored copy (there is no standalone botocore repo here).
if grep -qiE 'vendored/requests|awscli/botocore/vendored|botocore\.vendored\.requests' "$REPORT"; then FOUND=$((FOUND + 1)); HOP2_OK=1; fi
# general path framing (direct vs transitive) — required but not sufficient alone
if grep -qiE 'transitive|indirect|multi.hop|multi-hop|dependency chain' "$REPORT"; then FOUND=$((FOUND + 1)); fi

SCORE=$(awk "BEGIN {printf \"%.2f\", $FOUND/$TOTAL}")
if [ "$HOP1_OK" -eq 1 ] && [ "$HOP2_OK" -eq 1 ]; then PASSED=true; else PASSED=false; fi

printf '{"score": %s, "passed": %s, "reason": "Traced %d/%d transitive paths"}\n' "$SCORE" "$PASSED" "$FOUND" "$TOTAL"
