#!/usr/bin/env bash
# check_root_cause.sh — agent attributes the mis-framing to hyper's HTTP/1 chunked
# size decoder (NOT reqwest), identifies the unchecked-overflow accumulation, and
# describes the reqwest -> hyper body traversal.
set -euo pipefail

WORKSPACE="${WORKSPACE:-/workspace}"

ALL_TEXT=""
# Score ONLY the agent's own deliverable under agent_output/. Do NOT scan
# workspace-root files: the harness plants /workspace/instruction.md (the task
# prompt, which contains the symptom keywords) there, so grepping it would let a
# no-op run pass this checkpoint on the prompt text alone. Skip symlinks (an
# agent could point one at an arbitrary host path) and cap per-file reads.
if [[ -d "$WORKSPACE/agent_output" ]]; then
    while IFS= read -r f; do
        [[ -f "$f" && ! -L "$f" ]] || continue
        ALL_TEXT="$ALL_TEXT $(head -c 200000 "$f" 2>/dev/null)"
    done < <(find "$WORKSPACE/agent_output" -maxdepth 2 -type f 2>/dev/null | head -20)
fi
LC="$(printf '%s' "$ALL_TEXT" | tr '[:upper:]' '[:lower:]')"

FOUND=0
TOTAL=3
# (a) attributes the throw site to hyper's HTTP/1 chunked decoder
if printf '%s' "$LC" | grep -q "hyper" && printf '%s' "$LC" | grep -Eq "chunk.?size|chunked (decoder|size|read|state)|read_size|chunkedstate|decode\.rs"; then
    FOUND=$((FOUND + 1))
fi
# (b) identifies the unchecked overflow of the size accumulator
if printf '%s' "$LC" | grep -q "overflow" && printf '%s' "$LC" | grep -Eq "u64|checked_mul|checked_add|multiply|accumulat|wrap"; then
    FOUND=$((FOUND + 1))
fi
# (c) describes the reqwest -> hyper body traversal
if printf '%s' "$LC" | grep -Eq "httpbody|hyper::body|hyper body|body stream|::chunk\(\)|response body"; then
    FOUND=$((FOUND + 1))
fi

SCORE=$(awk "BEGIN {printf \"%.2f\", $FOUND/$TOTAL}")
PASSED=$([ "$FOUND" -ge 2 ] && echo true || echo false)
printf '{"score": %s, "passed": %s, "detail": "root-cause indicators %d/%d (hyper-chunk-attribution, overflow, body-traversal)"}\n' "$SCORE" "$PASSED" "$FOUND" "$TOTAL"
