#!/usr/bin/env bash
# check_root_cause.sh — agent attributes the decompression failure to netty's gzip
# inflater extra-field parsing (NOT reactor-netty), describes the sentinel/OR +
# byte-order bug, and the client's decompressor wiring.
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
# (a) attributes the mis-parse to netty's gzip inflater/decoder, not reactor-netty
if printf '%s' "$LC" | grep -Eq "\bnetty\b" \
    && printf '%s' "$LC" | grep -Eq "jdkzlibdecoder|zlib decoder|gzip (inflater|decoder|header)|gzip inflater"; then
    FOUND=$((FOUND + 1))
fi
# (b) describes the extra-field bug: sentinel/OR (xlen stays -1) and/or wrong byte order
if printf '%s' "$LC" | grep -Eq "xlen|extra.?field|fextra" \
    && printf '%s' "$LC" | grep -Eq "sentinel|-1|or\b|\|=|little.?endian|big.?endian|byte order|byte-order"; then
    FOUND=$((FOUND + 1))
fi
# (c) wiring: enabling decompression inserts the decompressor which builds the decoder
if printf '%s' "$LC" | grep -Eq "httpcontentdecompressor|decompressor|httpclientconfig|pipeline|decompress"; then
    FOUND=$((FOUND + 1))
fi

SCORE=$(awk "BEGIN {printf \"%.2f\", $FOUND/$TOTAL}")
PASSED=$([ "$FOUND" -ge 2 ] && echo true || echo false)
printf '{"score": %s, "passed": %s, "detail": "root-cause indicators %d/%d (netty-attribution, extra-field-bug, decompressor-wiring)"}\n' "$SCORE" "$PASSED" "$FOUND" "$TOTAL"
