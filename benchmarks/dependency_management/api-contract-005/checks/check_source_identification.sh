#!/usr/bin/env bash
set -euo pipefail

REPORT="${WORKSPACE:-/workspace}/analysis/IMPACT_REPORT.md"
if [[ ! -f "$REPORT" ]]; then
  printf '{"score": 0.0, "passed": false, "reason": "IMPACT_REPORT.md not found"}\n'
  exit 0
fi

FOUND=0
TOTAL=4
# Repo-specific artifacts only — the prompt never names the file, the
# replacement call, the Go type distinction, or a second producer file, so
# none of these are answerable from the prompt text alone.
if grep -qiE 'internal/status/status\.go' "$REPORT"; then FOUND=$((FOUND + 1)); fi
if grep -qiE 'UnmarshalNew|anypb\.Any|any\.UnmarshalNew' "$REPORT"; then FOUND=$((FOUND + 1)); fi
if grep -qiE 'MessageV1|MessageV2|protoadapt' "$REPORT"; then FOUND=$((FOUND + 1)); fi
if grep -qiE 'encoding/proto/proto\.go|binarylog/method_logger\.go|binarylog/sink\.go|internal/pretty/pretty\.go|grpclb\.go|grpclb_remote_balancer\.go|handler_server\.go|http2_server\.go|interop/test_utils\.go' "$REPORT"; then FOUND=$((FOUND + 1)); fi

SCORE=$(awk "BEGIN {printf \"%.2f\", $FOUND/$TOTAL}")
if [ "$FOUND" -ge 3 ]; then PASSED=true; else PASSED=false; fi

printf '{"score": %s, "passed": %s, "reason": "Identified %d/%d source elements"}\n' "$SCORE" "$PASSED" "$FOUND" "$TOTAL"

