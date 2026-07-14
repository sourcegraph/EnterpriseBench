#!/usr/bin/env bash
# check_localization.sh — the agent must attribute the mangled IPv6 zone id to
# yarl (the URL library) and name the host-encoding origin, and describe the
# cross-repo traversal from the aiohttp connector into yarl.
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
# (a) attributes the defect to yarl and names the host-encoding origin
if printf '%s' "$LC" | grep -q "yarl" && printf '%s' "$LC" | grep -Eq "_url\.py|_encode_host|host property|host accessor|host_subcomponent"; then
    FOUND=$((FOUND + 1))
fi
# (b) identifies the %25 -> % zone-separator decoding as the defect
if printf '%s' "$LC" | grep -q "%25" && printf '%s' "$LC" | grep -Eq "zone|scope"; then
    FOUND=$((FOUND + 1))
fi
# (c) describes the aiohttp connector -> raw_host -> resolver traversal
if printf '%s' "$LC" | grep -Eq "connector|_resolve_host|_create_direct_connection|update_host" && printf '%s' "$LC" | grep -Eq "raw_host|getaddrinfo|resolver"; then
    FOUND=$((FOUND + 1))
fi

SCORE=$(awk "BEGIN {printf \"%.2f\", $FOUND/$TOTAL}")
PASSED=$([ "$FOUND" -ge 2 ] && echo true || echo false)
printf '{"score": %s, "passed": %s, "detail": "localization indicators %d/%d (yarl-attribution, %%25-zone-decoding, connector->resolver traversal)"}\n' "$SCORE" "$PASSED" "$FOUND" "$TOTAL"
