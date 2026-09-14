#!/usr/bin/env bash
# check_patch_semantics.sh — the patch must strip the proxy credential header on
# a cross-host redirect, scoped to the redirect handler rather than disabling
# redirects or a client-side workaround.
set -euo pipefail

WORKSPACE="${WORKSPACE:-/workspace}"
REPO="$WORKSPACE/follow-redirects"

# ADDED/REMOVED carry the agent's diff with comments stripped, so a token
# inside a comment reads as absent: appending one comment line at the fix site
# used to score 1.0 here (bead jn73.2.7, D2).
ADDED="$(python3 -m eb_verify.plugins.patch_evidence code \
    --repo "$REPO" --path index.js --lang javascript --added)"

# Case-insensitive match for the proxy header being added to the strip set.
if printf '%s' "$ADDED" | grep -Eiq "proxy-?authorization|proxy-\)\?authorization|\(\?:proxy-\)"; then
    printf '{"score": 1.0, "passed": true, "detail": "patch strips the proxy-authorization header on cross-host redirect"}\n'
elif printf '%s' "$ADDED" | grep -Eiq "proxy"; then
    printf '{"score": 0.6, "passed": true, "detail": "patch references proxy but stripping of the proxy credential header is not clearly shown"}\n'
else
    printf '{"score": 0.0, "passed": false, "detail": "patch does not strip the proxy credential header at the redirect handler"}\n'
fi
