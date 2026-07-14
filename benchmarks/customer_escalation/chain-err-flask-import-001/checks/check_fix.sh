#!/usr/bin/env bash
# Milestone/checkpoint verifier: check that a fix was applied.
# Receives workspace path as $1.
set -euo pipefail

WORKSPACE="${1:-.}"

# Check for FIX_SUMMARY.md
FIX_SUMMARY="$WORKSPACE/flask/FIX_SUMMARY.md"
HAS_SUMMARY=0
if [ -f "$FIX_SUMMARY" ] && [ "$(wc -c < "$FIX_SUMMARY")" -gt 50 ]; then
    HAS_SUMMARY=1
fi

# Check for actual code changes (any modified .py file in flask/src)
HAS_CODE_CHANGES=0
if [ -d "$WORKSPACE/flask/.git" ]; then
    # --no-textconv/--no-ext-diff: the repo being diffed is the agent's, and a
    # .gitattributes diff driver would otherwise run agent-chosen code inside the
    # grader (bead EnterpriseBench-8krz5).
    CHANGED_FILES=$(cd "$WORKSPACE/flask" && git diff --no-textconv --no-ext-diff --name-only HEAD~1 2>/dev/null | grep "\.py$" || true)
    if [ -n "$CHANGED_FILES" ]; then
        HAS_CODE_CHANGES=1
    fi
fi

if [ "$HAS_SUMMARY" -eq 1 ] && [ "$HAS_CODE_CHANGES" -eq 1 ]; then
    echo '{"score": 1.0, "message": "Fix summary and code changes present"}'
    exit 0
elif [ "$HAS_SUMMARY" -eq 1 ]; then
    echo '{"score": 0.6, "message": "Fix summary present but no code changes detected"}'
    exit 0
elif [ "$HAS_CODE_CHANGES" -eq 1 ]; then
    echo '{"score": 0.5, "message": "Code changes present but no fix summary"}'
    exit 0
else
    echo '{"score": 0.0, "message": "No fix summary or code changes found"}'
    exit 1
fi
