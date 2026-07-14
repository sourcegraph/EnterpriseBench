#!/usr/bin/env bash
# check_root_cause.sh — agent attributes the prototype-pollution sink to the qs
# query-string library (NOT express / body-parser), names the recursive key
# expander, and describes the two paths that feed it.
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
# (a) attributes the sink to qs and to the prototype (__proto__ / prototype pollution)
if printf '%s' "$LC" | grep -Eq "\bqs\b|library qs| qs " && printf '%s' "$LC" | grep -Eq "__proto__|prototype pollution|object.prototype|prototype key"; then
    FOUND=$((FOUND + 1))
fi
# (b) names the recursive key-expansion routine / unguarded assignment
if printf '%s' "$LC" | grep -Eq "parseobject|parsekeys|key expansion|recursive.*(parse|expand)|nested key"; then
    FOUND=$((FOUND + 1))
fi
# (c) describes the two feed paths: extended body parser + framework query layer
if printf '%s' "$LC" | grep -Eq "extended|urlencoded|require\('?qs|dynamic require" && printf '%s' "$LC" | grep -Eq "query|qs\.parse|middleware"; then
    FOUND=$((FOUND + 1))
fi

SCORE=$(awk "BEGIN {printf \"%.2f\", $FOUND/$TOTAL}")
PASSED=$([ "$FOUND" -ge 2 ] && echo true || echo false)
printf '{"score": %s, "passed": %s, "detail": "root-cause indicators %d/%d (qs-attribution, key-expander, dual-path)"}\n' "$SCORE" "$PASSED" "$FOUND" "$TOTAL"
