#!/usr/bin/env bash
# check_patch_site.sh — the uncommitted diff must land in urllib3's response
# module (src/urllib3/response.py, the real fix site), not only in requests.
set -euo pipefail

WORKSPACE="${WORKSPACE:-/workspace}"
REPO="$WORKSPACE/urllib3"
TARGET="src/urllib3/response.py"

if [[ ! -d "$REPO/.git" ]]; then
    printf '{"score": 0.0, "passed": false, "detail": "urllib3 workspace repo not found at %s"}\n' "$REPO"
    exit 0
fi

CHANGED="$(git -C "$REPO" diff --name-only HEAD 2>/dev/null)"

if printf '%s\n' "$CHANGED" | grep -Fxq "$TARGET"; then
    printf '{"score": 1.0, "passed": true, "detail": "diff modifies urllib3 response.py (real fix site for the content-decoder registry)"}\n'
elif [[ -n "${CHANGED//[[:space:]]/}" ]]; then
    printf '{"score": 0.2, "passed": false, "detail": "urllib3 has a diff but it does not touch the response decoder fix site"}\n'
else
    printf '{"score": 0.0, "passed": false, "detail": "no uncommitted change in urllib3"}\n'
fi
