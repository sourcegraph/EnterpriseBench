#!/usr/bin/env bash
# check_localization.sh — the agent must attribute the bare KeyError to kombu's
# accept-content preparation (NOT Celery), name SerializerNotInstalled as the
# expected error, and describe the Celery -> kombu Consumer traversal.
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
# (a) attributes to kombu's accept-content preparation
if printf '%s' "$LC" | grep -q "kombu" && printf '%s' "$LC" | grep -Eq "prepare_accept_content|accept.content|serialization\.py|name_to_type"; then
    FOUND=$((FOUND + 1))
fi
# (b) identifies the missing SerializerNotInstalled vs bare KeyError
if printf '%s' "$LC" | grep -q "serializernotinstalled" && printf '%s' "$LC" | grep -q "keyerror"; then
    FOUND=$((FOUND + 1))
fi
# (c) describes the Celery consumer wiring -> kombu Consumer traversal
if printf '%s' "$LC" | grep -Eq "accept_content|taskconsumer|consumer" && printf '%s' "$LC" | grep -Eq "kombu|messaging|consumer\.__init__|prepare_accept_content"; then
    FOUND=$((FOUND + 1))
fi

SCORE=$(awk "BEGIN {printf \"%.2f\", $FOUND/$TOTAL}")
PASSED=$([ "$FOUND" -ge 2 ] && echo true || echo false)
printf '{"score": %s, "passed": %s, "detail": "localization indicators %d/%d (kombu-attribution, SerializerNotInstalled-vs-KeyError, celery->kombu traversal)"}\n' "$SCORE" "$PASSED" "$FOUND" "$TOTAL"
