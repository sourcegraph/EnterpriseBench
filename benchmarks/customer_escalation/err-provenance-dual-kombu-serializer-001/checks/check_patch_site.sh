#!/usr/bin/env bash
# check_patch_site.sh — the uncommitted diff must land in kombu's serialization
# module (kombu/serialization.py, the real fix site), not only in Celery.
set -euo pipefail

WORKSPACE="${WORKSPACE:-/workspace}"
REPO="$WORKSPACE/kombu"
TARGET="kombu/serialization.py"

if [[ ! -d "$REPO/.git" ]]; then
    printf '{"score": 0.0, "passed": false, "detail": "kombu workspace repo not found at %s"}\n' "$REPO"
    exit 0
fi

CHANGED="$(git -C "$REPO" diff --name-only HEAD 2>/dev/null; git -C "$REPO" diff --cached --name-only 2>/dev/null)"

if printf '%s\n' "$CHANGED" | grep -Fxq "$TARGET"; then
    printf '{"score": 1.0, "passed": true, "detail": "diff modifies kombu serialization.py (real fix site for accept-content preparation)"}\n'
elif [[ -n "$(printf '%s' "$CHANGED" | tr -d '[:space:]')" ]]; then
    printf '{"score": 0.2, "passed": false, "detail": "kombu has a diff but it does not touch the serialization fix site"}\n'
else
    printf '{"score": 0.0, "passed": false, "detail": "no uncommitted change in kombu"}\n'
fi
