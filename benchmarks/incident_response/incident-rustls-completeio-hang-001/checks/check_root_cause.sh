#!/usr/bin/env bash
# check_root_cause.sh — agent attributes the busy loop to rustls's blocking
# IO-completion routine (NOT ureq), identifies the no-progress non-termination,
# and describes the ureq -> rustls stream traversal.
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
# (a) attributes the loop to rustls's complete_io / IO-completion routine
if printf '%s' "$LC" | grep -q "rustls" && printf '%s' "$LC" | grep -Eq "complete_io|completion loop|io.?completion|conn\.rs"; then
    FOUND=$((FOUND + 1))
fi
# (b) identifies the no-progress non-termination
if printf '%s' "$LC" | grep -Eq "wants_write|wants_read|no progress|no further progress|progress is impossible|neither.*read.*write|busy loop|spin"; then
    FOUND=$((FOUND + 1))
fi
# (c) describes the ureq -> rustls stream traversal
if printf '%s' "$LC" | grep -Eq "streamowned|stream adapter|blocking stream|readwrite|clientconnection" || { printf '%s' "$LC" | grep -q "ureq" && printf '%s' "$LC" | grep -q "rustls"; }; then
    FOUND=$((FOUND + 1))
fi

SCORE=$(awk "BEGIN {printf \"%.2f\", $FOUND/$TOTAL}")
PASSED=$([ "$FOUND" -ge 2 ] && echo true || echo false)
printf '{"score": %s, "passed": %s, "detail": "root-cause indicators %d/%d (rustls-completeio-attribution, no-progress, stream-traversal)"}\n' "$SCORE" "$PASSED" "$FOUND" "$TOTAL"
