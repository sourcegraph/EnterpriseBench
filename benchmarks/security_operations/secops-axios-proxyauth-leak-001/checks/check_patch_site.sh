#!/usr/bin/env bash
# check_patch_site.sh — the uncommitted diff must land in follow-redirects'
# entry module (the real stripping site), not only in axios.
set -euo pipefail

WORKSPACE="${WORKSPACE:-/workspace}"
REPO="$WORKSPACE/follow-redirects"
TARGET="index.js"

if [[ ! -d "$REPO/.git" ]]; then
    printf '{"score": 0.0, "passed": false, "detail": "follow-redirects workspace repo not found at %s"}\n' "$REPO"
    exit 0
fi

CHANGED="$(git -C "$REPO" diff --name-only HEAD 2>/dev/null; git -C "$REPO" diff --cached --name-only 2>/dev/null)"

if printf '%s\n' "$CHANGED" | grep -Fxq "$TARGET"; then
    printf '{"score": 1.0, "passed": true, "detail": "diff modifies follow-redirects index.js (real stripping site)"}\n'
elif [[ -n "$(printf '%s' "$CHANGED" | tr -d '[:space:]')" ]]; then
    printf '{"score": 0.2, "passed": false, "detail": "follow-redirects has a diff but it does not touch the redirect entry module"}\n'
else
    printf '{"score": 0.0, "passed": false, "detail": "no uncommitted change in follow-redirects"}\n'
fi
