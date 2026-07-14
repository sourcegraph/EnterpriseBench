#!/usr/bin/env bash
# check_root_cause.sh — agent attributes the 500 to spring-core's mime-type text
# parser off-by-one (NOT the spring-boot welcome-page handler), names the
# unchecked-vs-checked exception mismatch, and describes the Accept-header traversal.
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
# (a) attributes the throw to spring-core / the mime-type parser, not spring-boot's handler
if printf '%s' "$LC" | grep -Eq "spring-core|spring core|spring-framework|mimetypeutils|mime.?type parser|mime type parser"; then
    FOUND=$((FOUND + 1))
fi
# (b) unchecked index exception vs checked invalid-media-type; the off-by-one / charAt(-1)
if printf '%s' "$LC" | grep -Eq "stringindexoutofbounds|index out of bounds|charat\(-1\)|charat\(nextindex|off.?by.?one|negative index|before index 0" \
    && printf '%s' "$LC" | grep -Eq "invalidmediatype|invalidmimetype|invalid media type|invalid mime type|checked exception"; then
    FOUND=$((FOUND + 1))
fi
# (c) traversal via the Accept-header parse into media-type parsing
if printf '%s' "$LC" | grep -Eq "accept header|accept-header|parsemediatype|parsemimetype|welcome.?page|getacceptedmediatypes"; then
    FOUND=$((FOUND + 1))
fi

SCORE=$(awk "BEGIN {printf \"%.2f\", $FOUND/$TOTAL}")
PASSED=$([ "$FOUND" -ge 2 ] && echo true || echo false)
printf '{"score": %s, "passed": %s, "detail": "root-cause indicators %d/%d (core-parser-attribution, unchecked-vs-checked-offbyone, accept-header-traversal)"}\n' "$SCORE" "$PASSED" "$FOUND" "$TOTAL"
