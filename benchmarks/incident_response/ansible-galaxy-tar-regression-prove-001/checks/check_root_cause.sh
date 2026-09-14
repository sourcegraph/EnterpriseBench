#!/usr/bin/env bash
# check_root_cause.sh — verify agent identified the tar extraction fragility
set -euo pipefail

WORKSPACE="${WORKSPACE:-/workspace}"

# Check if agent referenced the key files
FOUND=0
TOTAL=2

# Look for any agent output referencing the key files
ALL_TEXT=""
while IFS= read -r f; do
    ALL_TEXT="$ALL_TEXT $(cat "$f" 2>/dev/null)"
done < <(find "$WORKSPACE" -maxdepth 1 \( -name "*.py" -o -name "*.md" -o -name "*.json" \) 2>/dev/null | head -5)
# Also check agent_output/ directory
if [[ -d "$WORKSPACE/agent_output" ]]; then
    ALL_TEXT="$ALL_TEXT $(cat "$WORKSPACE/agent_output"/* 2>/dev/null)"
fi

# Indicator 1: names the actual source file(s), not just the generic
# "Galaxy collection installation code" phrasing already in the prompt.
if echo "$ALL_TEXT" | grep -qiE "concrete_artifact_manager\.py|galaxy/collection/__init__\.py|collection/__init__\.py"; then
    FOUND=$((FOUND + 1))
fi
# Indicator 2: names the specific fragile mechanism by its real symbol —
# generic terms like "tar member"/"getmember"/"tarfile" already appear in
# the prompt narrative and don't prove repo access on their own.
if echo "$ALL_TEXT" | grep -qiE "_ansible_normalized_cache|_extract_tar_dir|install_artifact|_get_tar_file_member|removesuffix"; then
    FOUND=$((FOUND + 1))
fi

SCORE=$(awk "BEGIN {t=($TOTAL>1?$TOTAL:1); printf \"%.2f\", $FOUND/t}")
PASSED=$([ "$FOUND" -ge 1 ] && echo true || echo false)
printf '{"score": %s, "passed": %s, "detail": "Found %d/%d root cause indicators"}\n' "$SCORE" "$PASSED" "$FOUND" "$TOTAL"
