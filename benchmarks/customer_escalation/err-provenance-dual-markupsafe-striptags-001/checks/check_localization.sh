#!/usr/bin/env bash
# check_localization.sh — the agent must attribute the surviving multi-line tag to
# markupsafe's strip regexes (NOT jinja2), note the missing DOTALL / newline
# handling, and describe the jinja2 striptags filter -> Markup.striptags traversal.
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
# (a) attributes to markupsafe's strip regexes
if printf '%s' "$LC" | grep -q "markupsafe" && printf '%s' "$LC" | grep -Eq "_strip_tags_re|_strip_comments_re|strip.*regex|regex.*strip|__init__\.py"; then
    FOUND=$((FOUND + 1))
fi
# (b) identifies the missing DOTALL / newline handling
if printf '%s' "$LC" | grep -Eq "dotall|newline|multi.?line|\[\\\\s\\\\s\]|does not match" ; then
    FOUND=$((FOUND + 1))
fi
# (c) describes the jinja2 striptags filter -> Markup.striptags traversal
if printf '%s' "$LC" | grep -Eq "striptags|do_striptags|filters\.py|default_filters" && printf '%s' "$LC" | grep -Eq "markup\.striptags|markup\(|markupsafe|escaping library"; then
    FOUND=$((FOUND + 1))
fi

SCORE=$(awk "BEGIN {printf \"%.2f\", $FOUND/$TOTAL}")
PASSED=$([ "$FOUND" -ge 2 ] && echo true || echo false)
printf '{"score": %s, "passed": %s, "detail": "localization indicators %d/%d (markupsafe-regex-attribution, missing-DOTALL/newline, striptags-filter traversal)"}\n' "$SCORE" "$PASSED" "$FOUND" "$TOTAL"
