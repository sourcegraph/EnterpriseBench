#!/usr/bin/env bash
# check_patch_semantics.sh — the patch must make the completion loop return when
# neither a read nor a write can make progress, rather than iterate again.
set -euo pipefail

WORKSPACE="${WORKSPACE:-/workspace}"
REPO="$WORKSPACE/rustls"

if [[ ! -d "$REPO/.git" ]]; then
    printf '{"score": 0.0, "passed": false, "detail": "rustls workspace repo not found"}\n'
    exit 0
fi

DIFF="$(git -C "$REPO" diff HEAD -- 'rustls/src/conn.rs' 2>/dev/null; git -C "$REPO" diff --cached -- 'rustls/src/conn.rs' 2>/dev/null)"
ADDED="$(printf '%s' "$DIFF" | grep '^+' | grep -v '^+++' || true)"

# No-progress guard: both wants_write and wants_read tested, and a return/break.
if printf '%s' "$ADDED" | grep -q "wants_write" && printf '%s' "$ADDED" | grep -q "wants_read"; then
    if printf '%s' "$ADDED" | grep -Eq "return|break"; then
        printf '{"score": 1.0, "passed": true, "detail": "patch returns from the completion loop when neither read nor write is wanted"}\n'
    else
        printf '{"score": 0.6, "passed": true, "detail": "patch tests wants_write/wants_read but the early return is not clearly shown"}\n'
    fi
elif printf '%s' "$ADDED" | grep -Eiq "no.?progress|progress.*impossible" && printf '%s' "$ADDED" | grep -Eq "return|break"; then
    printf '{"score": 0.6, "passed": true, "detail": "patch adds a no-progress return but does not clearly test both wants_write and wants_read"}\n'
else
    printf '{"score": 0.0, "passed": false, "detail": "patch does not add a no-progress bail-out to the completion loop"}\n'
fi
