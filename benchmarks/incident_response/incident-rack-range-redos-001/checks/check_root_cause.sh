#!/usr/bin/env bash
# check_root_cause.sh — agent attributes the hang to Rack's byte-range utility
# (NOT Sinatra), names the backtracking regex, and describes the send_file ->
# file server -> utility traversal.
set -euo pipefail

WORKSPACE="${WORKSPACE:-/workspace}"

ALL_TEXT=""
# Score ONLY the agent's own deliverable under agent_output/. The harness plants
# /workspace/instruction.md (the prompt, which contains the symptom keywords) at
# the workspace root, so scanning it would let a no-op run pass on prompt text.
if [[ -d "$WORKSPACE/agent_output" ]]; then
    while IFS= read -r f; do
                ALL_TEXT="$ALL_TEXT $(head -c 200000 "$f" 2>/dev/null)"
    done < <(find "$WORKSPACE/agent_output" -maxdepth 2 -type f 2>/dev/null | head -20)
fi
LC="$(printf '%s' "$ALL_TEXT" | tr '[:upper:]' '[:lower:]')"

FOUND=0
TOTAL=3
# (a) attributes the ReDoS to rack and the byte-range parser
if printf '%s' "$LC" | grep -Eq "\brack\b" && printf '%s' "$LC" | grep -Eq "byte.?range|get_byte_ranges|range.*(parse|regex)|range spec"; then
    FOUND=$((FOUND + 1))
fi
# (b) identifies catastrophic backtracking / ReDoS
if printf '%s' "$LC" | grep -Eq "backtrack|redos|catastrophic|exponential"; then
    FOUND=$((FOUND + 1))
fi
# (c) describes the send_file -> file server -> utility traversal
if printf '%s' "$LC" | grep -Eq "send_file|sinatra" && printf '%s' "$LC" | grep -Eq "rack::files|files\.rb|file server|serving|http_range"; then
    FOUND=$((FOUND + 1))
fi

SCORE=$(awk "BEGIN {printf \"%.2f\", $FOUND/$TOTAL}")
PASSED=$([ "$FOUND" -ge 2 ] && echo true || echo false)
printf '{"score": %s, "passed": %s, "detail": "root-cause indicators %d/%d (rack-attribution, redos, traversal)"}\n' "$SCORE" "$PASSED" "$FOUND" "$TOTAL"
