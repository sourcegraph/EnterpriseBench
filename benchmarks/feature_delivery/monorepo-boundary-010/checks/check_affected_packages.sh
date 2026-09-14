#!/usr/bin/env bash
# Checkpoint 1: Verify agent identified affected packages
set -euo pipefail

REPORT="${WORKSPACE:-/workspace}/rust/IMPACT_REPORT.md"
if [[ ! -f "$REPORT" ]]; then
  printf '{"score": 0.0, "passed": false, "reason": "IMPACT_REPORT.md not found"}\n'
  exit 0
fi

FOUND=0
TOTAL=4
# rustc_const_eval: interpreter-side files that actually change (per expected_solution.json)
if grep -qiE 'eval_context\.rs|eval_queries\.rs|rustc_const_eval/src' "$REPORT"; then FOUND=$((FOUND + 1)); fi
# rustc_metadata: encoder/decoder of the new metadata-based statics
if grep -qiE 'rmeta/encoder\.rs|rmeta/decoder|rustc_metadata/src/rmeta' "$REPORT"; then FOUND=$((FOUND + 1)); fi
# rustc_mir_transform: the GVN transform that consumes the new representation
if grep -qiE 'mir_transform/src/gvn\.rs|[^a-z]gvn\.rs' "$REPORT"; then FOUND=$((FOUND + 1)); fi
# miri: the shim that needs updating for the new representation
if grep -qiE 'tools/miri/src/shims/intrinsics|shims/intrinsics/mod\.rs' "$REPORT"; then FOUND=$((FOUND + 1)); fi

# Compute score as proper decimal
if [ "$TOTAL" -gt 0 ]; then
  SCORE=$(awk "BEGIN {printf \"%.2f\", $FOUND/$TOTAL}")
else
  SCORE="0.00"
fi
if [ "$FOUND" -ge 3 ]; then PASSED=true; else PASSED=false; fi

printf '{"score": %s, "passed": %s, "reason": "Found %d/%d affected packages"}\n' "$SCORE" "$PASSED" "$FOUND" "$TOTAL"
