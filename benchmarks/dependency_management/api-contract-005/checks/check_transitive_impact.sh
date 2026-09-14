#!/usr/bin/env bash
set -euo pipefail

REPORT="${WORKSPACE:-/workspace}/analysis/IMPACT_REPORT.md"
if [[ ! -f "$REPORT" ]]; then
  printf '{"score": 0.0, "passed": false, "reason": "IMPACT_REPORT.md not found"}\n'
  exit 0
fi

FOUND=0
TOTAL=3
# Repo-specific artifacts only — the prompt never names any of these files or
# symbols, so an agent can only cite them by actually tracing the code.
# 1. a propagation path beyond the direct Details()/WithDetails() callers
if grep -qiE 'grpclb\.go|grpclb_remote_balancer\.go|binarylog/method_logger\.go|binarylog/sink\.go|internal/pretty/pretty\.go|handler_server\.go|http2_server\.go|interop/test_utils\.go' "$REPORT"; then FOUND=$((FOUND + 1)); fi
# 2. the actual V1/V2 adapter mechanism behind the consumer fix
if grep -qiE 'protoadapt|MessageV1Of|MessageV2Of|UnmarshalNew' "$REPORT"; then FOUND=$((FOUND + 1)); fi
# 3. a specific consumer/test file that observes the transitive break
if grep -qiE 'status_ext_test\.go|status_equal\.go|internal/testutils' "$REPORT"; then FOUND=$((FOUND + 1)); fi

SCORE=$(awk "BEGIN {printf \"%.2f\", $FOUND/$TOTAL}")
if [ "$FOUND" -ge 2 ]; then PASSED=true; else PASSED=false; fi

printf '{"score": %s, "passed": %s, "reason": "Traced %d/%d transitive impact areas"}\n' "$SCORE" "$PASSED" "$FOUND" "$TOTAL"
