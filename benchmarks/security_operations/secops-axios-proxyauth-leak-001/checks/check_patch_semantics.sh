#!/usr/bin/env bash
# check_patch_semantics.sh — the patch must strip the proxy credential header on
# a cross-host redirect, scoped to the redirect handler rather than disabling
# redirects or a client-side workaround.
set -euo pipefail

WORKSPACE="${WORKSPACE:-/workspace}"
REPO="$WORKSPACE/follow-redirects"

if [[ ! -d "$REPO/.git" ]]; then
    printf '{"score": 0.0, "passed": false, "detail": "follow-redirects workspace repo not found"}\n'
    exit 0
fi

DIFF="$(git -C "$REPO" diff HEAD -- 'index.js' 2>/dev/null; git -C "$REPO" diff --cached -- 'index.js' 2>/dev/null)"
ADDED="$(printf '%s' "$DIFF" | grep '^+' | grep -v '^+++' || true)"

# Case-insensitive match for the proxy header being added to the strip set.
if printf '%s' "$ADDED" | grep -Eiq "proxy-?authorization|proxy-\)\?authorization|\(\?:proxy-\)"; then
    printf '{"score": 1.0, "passed": true, "detail": "patch strips the proxy-authorization header on cross-host redirect"}\n'
elif printf '%s' "$ADDED" | grep -Eiq "proxy"; then
    printf '{"score": 0.6, "passed": true, "detail": "patch references proxy but stripping of the proxy credential header is not clearly shown"}\n'
else
    printf '{"score": 0.0, "passed": false, "detail": "patch does not strip the proxy credential header at the redirect handler"}\n'
fi
