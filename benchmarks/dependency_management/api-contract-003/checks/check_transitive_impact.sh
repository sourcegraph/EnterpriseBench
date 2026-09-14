#!/usr/bin/env bash
set -euo pipefail

REPORT="${WORKSPACE:-/workspace}/analysis/IMPACT_REPORT.md"
if [[ ! -f "$REPORT" ]]; then
  printf '{"score": 0.0, "passed": false, "reason": "IMPACT_REPORT.md not found"}\n'
  exit 0
fi

FOUND=0
TOTAL=3
# Repo-specific artifacts only — the three-repo chain, "vendors", and
# "apiserver/kubelet" subsystem names are already given verbatim in the
# prompt and must not earn credit here.
# Must cross-reference the consumer-side fix PR
if grep -qiE 'kubernetes/kubernetes#57160|kubernetes[^0-9]{0,20}57160|#57160' "$REPORT"; then FOUND=$((FOUND + 1)); fi
# Must identify the transitive propagation mechanism (interceptor/middleware/wrapper), not just direct imports
if grep -qiE 'interceptor|middleware|wrapper.*transport|transport.*wrapper|forward.*transport|proxy.*transport' "$REPORT"; then FOUND=$((FOUND + 1)); fi
# Must name at least one specific breaking symbol whose type/signature change propagates
if grep -qiE 'ClientTransport|ServerTransport|NewClientTransport|transport\.Stream' "$REPORT"; then FOUND=$((FOUND + 1)); fi

SCORE=$(awk "BEGIN {printf \"%.2f\", $FOUND/$TOTAL}")
if [ "$FOUND" -ge 2 ]; then PASSED=true; else PASSED=false; fi

printf '{"score": %s, "passed": %s, "reason": "Traced %d/%d chain elements"}\n' "$SCORE" "$PASSED" "$FOUND" "$TOTAL"
