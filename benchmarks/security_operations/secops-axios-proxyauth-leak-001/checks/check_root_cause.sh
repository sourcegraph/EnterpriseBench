#!/usr/bin/env bash
# check_root_cause.sh — agent attributes the proxy-credential leak to the
# follow-redirects transport (NOT axios), names the header-stripping routine, and
# describes the axios proxy-header synthesis + transport substitution.
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
# (a) attributes retention to follow-redirects and the proxy credential header
if printf '%s' "$LC" | grep -q "follow-redirects" && printf '%s' "$LC" | grep -Eq "proxy-authorization|proxy authorization|proxy credential"; then
    FOUND=$((FOUND + 1))
fi
# (b) identifies the narrow stripping pattern: authorization|cookie omits proxy
if printf '%s' "$LC" | grep -Eq "removematchingheaders|strip|remove" && printf '%s' "$LC" | grep -Eq "authorization|cookie"; then
    FOUND=$((FOUND + 1))
fi
# (c) describes the axios synthesis + transport substitution
if printf '%s' "$LC" | grep -Eq "setproxy|proxy option|synthes" && printf '%s' "$LC" | grep -Eq "transport|adapter|httpfollow|httpsfollow"; then
    FOUND=$((FOUND + 1))
fi

SCORE=$(awk "BEGIN {printf \"%.2f\", $FOUND/$TOTAL}")
PASSED=$([ "$FOUND" -ge 2 ] && echo true || echo false)
printf '{"score": %s, "passed": %s, "detail": "root-cause indicators %d/%d (follow-redirects-attribution, narrow-strip, transport-swap)"}\n' "$SCORE" "$PASSED" "$FOUND" "$TOTAL"
