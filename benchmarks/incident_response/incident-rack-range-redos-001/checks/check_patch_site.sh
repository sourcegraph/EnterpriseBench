#!/usr/bin/env bash
# check_patch_site.sh — the uncommitted diff must land in Rack's utility module
# (the real byte-range parse site), not only in sinatra.
set -euo pipefail

WORKSPACE="${WORKSPACE:-/workspace}"
REPO="$WORKSPACE/rack"
TARGET="lib/rack/utils.rb"

if [[ ! -d "$REPO/.git" ]]; then
    printf '{"score": 0.0, "passed": false, "detail": "rack workspace repo not found at %s"}\n' "$REPO"
    exit 0
fi

CHANGED="$(git -C "$REPO" diff --name-only HEAD 2>/dev/null)"

if printf '%s\n' "$CHANGED" | grep -Fxq "$TARGET"; then
    printf '{"score": 1.0, "passed": true, "detail": "diff modifies rack lib/rack/utils.rb (real byte-range parse site)"}\n'
elif [[ -n "${CHANGED//[[:space:]]/}" ]]; then
    printf '{"score": 0.2, "passed": false, "detail": "rack has a diff but it does not touch the byte-range utility"}\n'
else
    printf '{"score": 0.0, "passed": false, "detail": "no uncommitted change in rack"}\n'
fi
