#!/usr/bin/env bash
# check_localization.sh — the agent must attribute the undecoded body to urllib3's
# response decoder (NOT the requests client), identify the x-gzip encoding, and
# describe the adapter -> pool -> response traversal across the repo boundary.
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
# (a) attributes to urllib3's response decoder / recognized-encodings
if printf '%s' "$LC" | grep -q "urllib3" && printf '%s' "$LC" | grep -Eq "content_decoders|_get_decoder|decoder|response\.py"; then
    FOUND=$((FOUND + 1))
fi
# (b) identifies the x-gzip encoding not being treated as gzip
if printf '%s' "$LC" | grep -q "x-gzip"; then
    FOUND=$((FOUND + 1))
fi
# (c) describes the requests adapter -> urllib3 pool/response traversal
if printf '%s' "$LC" | grep -Eq "adapter|httpadapter|urlopen|connectionpool|connection pool" && printf '%s' "$LC" | grep -Eq "decode_content|iter_content|\.content|resp\.text|response body"; then
    FOUND=$((FOUND + 1))
fi

SCORE=$(awk "BEGIN {printf \"%.2f\", $FOUND/$TOTAL}")
PASSED=$([ "$FOUND" -ge 2 ] && echo true || echo false)
printf '{"score": %s, "passed": %s, "detail": "localization indicators %d/%d (urllib3-decoder-attribution, x-gzip-encoding, adapter->pool traversal)"}\n' "$SCORE" "$PASSED" "$FOUND" "$TOTAL"
