#!/usr/bin/env bash
# check_patch_site.sh — the uncommitted diff must land in markupsafe's main module
# (src/markupsafe/__init__.py, where the strip regexes are compiled), not in jinja2.
set -euo pipefail

WORKSPACE="${WORKSPACE:-/workspace}"
REPO="$WORKSPACE/markupsafe"
TARGET="src/markupsafe/__init__.py"

if [[ ! -d "$REPO/.git" ]]; then
    printf '{"score": 0.0, "passed": false, "detail": "markupsafe workspace repo not found at %s"}\n' "$REPO"
    exit 0
fi

CHANGED="$(git -C "$REPO" diff --name-only HEAD 2>/dev/null; git -C "$REPO" diff --cached --name-only 2>/dev/null)"

if printf '%s\n' "$CHANGED" | grep -Fxq "$TARGET"; then
    printf '{"score": 1.0, "passed": true, "detail": "diff modifies markupsafe __init__.py (real fix site for the tag-strip regexes)"}\n'
elif [[ -n "$(printf '%s' "$CHANGED" | tr -d '[:space:]')" ]]; then
    printf '{"score": 0.2, "passed": false, "detail": "markupsafe has a diff but it does not touch the strip-regex fix site"}\n'
else
    printf '{"score": 0.0, "passed": false, "detail": "no uncommitted change in markupsafe"}\n'
fi
