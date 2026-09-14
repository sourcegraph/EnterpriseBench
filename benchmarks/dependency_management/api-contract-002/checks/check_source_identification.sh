#!/usr/bin/env bash
# Checkpoint 1: Verify agent identified removed type aliases
# Repo-specific artifacts only. "balancer.go"/"resolver.go"/"type alias"/"PR #3309"
# are already given verbatim in the prompt and must not earn credit on their own;
# the deprecated *symbol names* (from grpc-go's balancer.go/resolver.go source)
# and the "no replacement" fact are NOT in the prompt and require reading the code.
set -euo pipefail

REPORT="${WORKSPACE:-/workspace}/analysis/IMPACT_REPORT.md"
if [[ ! -f "$REPORT" ]]; then
  printf '{"score": 0.0, "passed": false, "reason": "IMPACT_REPORT.md not found"}\n'
  exit 0
fi

FOUND=0
TOTAL=4

# 1. Producer-side file: balancer.go
if grep -qiE 'balancer/balancer\.go|balancer\.go' "$REPORT"; then FOUND=$((FOUND + 1)); fi
# 2. Producer-side file: resolver.go
if grep -qiE 'resolver/resolver\.go|resolver\.go' "$REPORT"; then FOUND=$((FOUND + 1)); fi
# 3. At least 3 of the 4 removed symbols named specifically (not derivable from
#    the prompt, which only says "type aliases" generically)
SYMS=0
if grep -qiE 'balancer\.Picker' "$REPORT"; then SYMS=$((SYMS + 1)); fi
if grep -qiE 'balancer\.Builder' "$REPORT"; then SYMS=$((SYMS + 1)); fi
if grep -qiE 'resolver\.Builder' "$REPORT"; then SYMS=$((SYMS + 1)); fi
if grep -qiE 'resolver\.Resolver' "$REPORT"; then SYMS=$((SYMS + 1)); fi
if [ "$SYMS" -ge 3 ]; then FOUND=$((FOUND + 1)); fi
# 4. States there is no replacement symbol (the aliases were removed outright)
if grep -qiE '\(none\)|no (direct )?replacement|no equivalent|removed (entirely|outright|without (a )?replacement)' "$REPORT"; then FOUND=$((FOUND + 1)); fi

SCORE=$(awk "BEGIN {printf \"%.2f\", $FOUND/$TOTAL}")
if [ "$FOUND" -ge 3 ]; then PASSED=true; else PASSED=false; fi

printf '{"score": %s, "passed": %s, "reason": "Identified %d/%d source elements"}\n' "$SCORE" "$PASSED" "$FOUND" "$TOTAL"
