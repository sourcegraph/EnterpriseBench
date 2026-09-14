#!/usr/bin/env bash
set -euo pipefail

REPORT="${WORKSPACE:-/workspace}/analysis/IMPACT_REPORT.md"
if [[ ! -f "$REPORT" ]]; then
  printf '{"score": 0.0, "passed": false, "reason": "IMPACT_REPORT.md not found"}\n'
  exit 0
fi

CONTENT=$(cat "$REPORT" | tr '[:upper:]' '[:lower:]')
FOUND=0
TOTAL=3
# Repo-specific artifacts only — generic classification language ("compile",
# "fail to compile", "embed", "restructur") is already given in the prompt
# and must not earn credit on its own.
# 1. Names the actual affected consumer file/package (not in the prompt).
if echo "$CONTENT" | grep -qE 'rafthttp|http\.go'; then FOUND=$((FOUND + 1)); fi
# 2. States the canonical ground-truth classification: this is a pure
#    compile-time break, not a compile+runtime hybrid.
if echo "$CONTENT" | grep -qE 'compile_error|compile-time error|compile-time only|compile-only|purely compile|only compile.time'; then FOUND=$((FOUND + 1)); fi
# 3. Ties the classification to the producer-side mechanism (the added
#    internal interface method / internal.go), not just the generic "embed"
#    wording already in the prompt.
if echo "$CONTENT" | grep -qE 'internal\.go|internal/internal\.go|internal\(\)|unexported method|internal interface method'; then FOUND=$((FOUND + 1)); fi

SCORE=$(awk "BEGIN {printf \"%.2f\", $FOUND/$TOTAL}")
if [ "$FOUND" -ge 2 ]; then PASSED=true; else PASSED=false; fi

printf '{"score": %s, "passed": %s, "reason": "Classified %d/%d aspects"}\n' "$SCORE" "$PASSED" "$FOUND" "$TOTAL"
