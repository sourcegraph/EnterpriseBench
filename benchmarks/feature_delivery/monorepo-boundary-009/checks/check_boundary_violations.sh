#!/usr/bin/env bash
# Checkpoint 3: Verify boundary violation identification
set -euo pipefail

REPORT="${WORKSPACE:-/workspace}/rust/IMPACT_REPORT.md"
if [[ ! -f "$REPORT" ]]; then
  printf '{"score": 0.0, "passed": false, "reason": "IMPACT_REPORT.md not found"}\n'
  exit 0
fi

FOUND=0
TOTAL=2
# Require a specific downstream Cargo.toml path (not just the generic word
# "Cargo.toml", which appears verbatim in the prompt).
if grep -qE 'rustc_(ast_passes|builtin_macros|codegen_llvm|codegen_gcc)/Cargo\.toml' "$REPORT"; then FOUND=$((FOUND + 1)); fi
# Require a specific boundary-violation source file cited in the expected
# solution (old crate file being hollowed out, or a downstream import site) —
# not just the bare crate name "rustc_attr", which also appears in the prompt.
if grep -qE 'rustc_attr/src/builtin\.rs|ast_validation\.rs|codegen_llvm/src/attributes\.rs|codegen_gcc/src/attributes\.rs|builtin_macros/src/cfg\.rs|coerce_pointee\.rs|deriving/generic/mod\.rs' "$REPORT"; then FOUND=$((FOUND + 1)); fi

if [ "$TOTAL" -gt 0 ]; then
  SCORE=$(awk "BEGIN {printf \"%.2f\", $FOUND/$TOTAL}")
else
  SCORE="0.00"
fi
if [ "$FOUND" -ge 1 ]; then PASSED=true; else PASSED=false; fi

printf '{"score": %s, "passed": %s, "reason": "Identified %d/%d boundary violation locations"}\n' "$SCORE" "$PASSED" "$FOUND" "$TOTAL"
