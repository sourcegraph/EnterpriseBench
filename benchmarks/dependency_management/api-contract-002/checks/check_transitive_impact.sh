#!/usr/bin/env bash
# Checkpoint 3: Verify agent traced transitive impact beyond direct importers.
# Generic terms like "custom balancer", "picker", "resolver" are already in the
# prompt and must not earn credit on their own; require the propagation
# mechanism (interceptor/middleware/wrapper), the specific consumer file(s),
# and the consumer-side fix PRs, none of which appear in the prompt.
set -euo pipefail

REPORT="${WORKSPACE:-/workspace}/analysis/IMPACT_REPORT.md"
if [[ ! -f "$REPORT" ]]; then
  printf '{"score": 0.0, "passed": false, "reason": "IMPACT_REPORT.md not found"}\n'
  exit 0
fi

FOUND=0
TOTAL=3
# Must name the propagation mechanism (forwarding/middleware/interceptor/wrapper)
if grep -qiE 'interceptor|middleware|wrapper|forward(ing|s|ed)?' "$REPORT"; then FOUND=$((FOUND + 1)); fi
# Must cite a specific consumer file (not just the generic "custom balancer/picker/resolver")
if grep -qiE 'roundrobin_balanced|picker/err\.go|resolver/endpoint|endpoint\.go' "$REPORT"; then FOUND=$((FOUND + 1)); fi
# Must cross-reference the consumer-side fix PR(s)
if grep -qiE 'etcd-io/etcd#11564|etcd-io/etcd#12671|#11564|#12671' "$REPORT"; then FOUND=$((FOUND + 1)); fi

SCORE=$(awk "BEGIN {printf \"%.2f\", $FOUND/$TOTAL}")
if [ "$FOUND" -ge 2 ]; then PASSED=true; else PASSED=false; fi

printf '{"score": %s, "passed": %s, "reason": "Traced %d/%d dependency chain components"}\n' "$SCORE" "$PASSED" "$FOUND" "$TOTAL"
