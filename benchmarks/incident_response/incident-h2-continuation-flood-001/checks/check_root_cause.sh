#!/usr/bin/env bash
# check_root_cause.sh — agent attributes the resource exhaustion to h2's HTTP/2
# frame reader / header-block reassembly (NOT hyper), identifies the unbounded
# CONTINUATION handling, and describes the hyper -> h2 traversal.
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
# (a) attributes the site to h2's frame reader / header-block reassembly
if printf '%s' "$LC" | grep -Eq "\bh2\b|h2 crate" && printf '%s' "$LC" | grep -Eq "frame.?read|framed_read|frame reader|header block|reassembl|codec"; then
    FOUND=$((FOUND + 1))
fi
# (b) identifies the unbounded CONTINUATION handling
if printf '%s' "$LC" | grep -q "continuation" && printf '%s' "$LC" | grep -Eq "unbounded|no (cap|limit|bound)|without (a )?(cap|limit|bound)|not (bounded|limited)|count|flood"; then
    FOUND=$((FOUND + 1))
fi
# (c) describes the hyper -> h2 server traversal
if printf '%s' "$LC" | grep -q "hyper" && printf '%s' "$LC" | grep -Eq "h2::server|handshake|connection future|end_headers|builder"; then
    FOUND=$((FOUND + 1))
fi

SCORE=$(awk "BEGIN {printf \"%.2f\", $FOUND/$TOTAL}")
PASSED=$([ "$FOUND" -ge 2 ] && echo true || echo false)
printf '{"score": %s, "passed": %s, "detail": "root-cause indicators %d/%d (h2-reader-attribution, unbounded-continuation, hyper-h2-traversal)"}\n' "$SCORE" "$PASSED" "$FOUND" "$TOTAL"
