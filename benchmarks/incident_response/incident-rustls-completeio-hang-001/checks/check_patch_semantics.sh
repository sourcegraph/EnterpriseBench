#!/usr/bin/env bash
# check_patch_semantics.sh — the patch must make the completion loop return when
# neither a read nor a write can make progress, rather than iterate again.
set -euo pipefail

WORKSPACE="${WORKSPACE:-/workspace}"
REPO="$WORKSPACE/rustls"

# ADDED/REMOVED carry the agent's diff with comments stripped, so a token
# inside a comment reads as absent: appending one comment line at the fix site
# used to score 1.0 here (bead jn73.2.7, D2).
ADDED="$(python3 -m eb_verify.plugins.patch_evidence code \
    --repo "$REPO" --path rustls/src/conn.rs --lang rust --added)"

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
