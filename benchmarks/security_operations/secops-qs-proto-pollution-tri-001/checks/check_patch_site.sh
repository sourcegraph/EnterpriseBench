#!/usr/bin/env bash
# check_patch_site.sh — the uncommitted diff must land in qs's recursive parse
# module (the real sink), not only in express or body-parser.
set -euo pipefail

WORKSPACE="${WORKSPACE:-/workspace}"
REPO="$WORKSPACE/qs"
TARGET="lib/parse.js"

if [[ ! -d "$REPO/.git" ]]; then
    printf '{"score": 0.0, "passed": false, "detail": "qs workspace repo not found at %s"}\n' "$REPO"
    exit 0
fi

CHANGED="$(git -C "$REPO" diff --name-only HEAD 2>/dev/null; git -C "$REPO" diff --cached --name-only 2>/dev/null)"

if printf '%s\n' "$CHANGED" | grep -Fxq "$TARGET"; then
    printf '{"score": 1.0, "passed": true, "detail": "diff modifies qs lib/parse.js (real pollution sink)"}\n'
elif [[ -n "$(printf '%s' "$CHANGED" | tr -d '[:space:]')" ]]; then
    printf '{"score": 0.2, "passed": false, "detail": "qs has a diff but it does not touch the recursive parse module"}\n'
else
    printf '{"score": 0.0, "passed": false, "detail": "no uncommitted change in qs"}\n'
fi
