#!/usr/bin/env bash
# check_root_cause.sh — agent attributes the connection leak to httpcore5's
# connection-pool lease future (NOT the httpclient5 connection manager) and
# describes the delegation traversal and the cancel/timeout race.
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
# (a) attributes the drop to httpcore5's pool (StrictConnPool/LaxConnPool), not httpclient5's manager
if printf '%s' "$LC" | grep -Eq "httpcore5|httpcore 5|httpcomponents-core" \
    && printf '%s' "$LC" | grep -Eq "strictconnpool|laxconnpool|conn.?pool|connection.?pool"; then
    FOUND=$((FOUND + 1))
fi
# (b) identifies the cancel/timeout race: entry delivered while timing out is dropped instead of returned
if printf '%s' "$LC" | grep -Eq "cancel" \
    && printf '%s' "$LC" | grep -Eq "timeout|timeoutexception" \
    && printf '%s' "$LC" | grep -Eq "race|delivered|dropped|leak|already set|not released"; then
    FOUND=$((FOUND + 1))
fi
# (c) describes the manager->interface->core-pool delegation traversal
if printf '%s' "$LC" | grep -Eq "poolinghttpclientconnectionmanager|connection manager|managedconnpool|poolconcurrencypolicy|delegat"; then
    FOUND=$((FOUND + 1))
fi

SCORE=$(awk "BEGIN {printf \"%.2f\", $FOUND/$TOTAL}")
PASSED=$([ "$FOUND" -ge 2 ] && echo true || echo false)
printf '{"score": %s, "passed": %s, "detail": "root-cause indicators %d/%d (core-attribution, cancel-race, delegation-traversal)"}\n' "$SCORE" "$PASSED" "$FOUND" "$TOTAL"
