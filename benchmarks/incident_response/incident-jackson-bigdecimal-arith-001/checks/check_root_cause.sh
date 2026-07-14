#!/usr/bin/env bash
# check_root_cause.sh — agent attributes the uncaught exception to jackson-core's
# big-decimal text parser (NOT jackson-databind) and describes the traversal.
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
# (a) names jackson-core / the big-decimal parser as the throw site
if printf '%s' "$LC" | grep -q "jackson-core" && printf '%s' "$LC" | grep -Eq "bigdecimalparser|big.?decimal parser|big decimal parser"; then
    FOUND=$((FOUND + 1))
fi
# (b) identifies the missing/too-narrow catch: ArithmeticException vs NumberFormatException
if printf '%s' "$LC" | grep -q "arithmeticexception" && printf '%s' "$LC" | grep -q "numberformatexception"; then
    FOUND=$((FOUND + 1))
fi
# (c) describes the databind->core traversal via the decimal accessor
if printf '%s' "$LC" | grep -Eq "getdecimalvalue|numberinput|decimal deserializer|numberdeserializers"; then
    FOUND=$((FOUND + 1))
fi

SCORE=$(awk "BEGIN {printf \"%.2f\", $FOUND/$TOTAL}")
PASSED=$([ "$FOUND" -ge 2 ] && echo true || echo false)
printf '{"score": %s, "passed": %s, "detail": "root-cause indicators %d/%d (core-attribution, catch-widening, traversal)"}\n' "$SCORE" "$PASSED" "$FOUND" "$TOTAL"
