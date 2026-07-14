#!/usr/bin/env bash
# check_patch_site.sh — the uncommitted diff must land in h2's HTTP/2 frame reader
# (the real fix site), not only in hyper.
set -euo pipefail

WORKSPACE="${WORKSPACE:-/workspace}"
REPO="$WORKSPACE/h2"
TARGET="src/codec/framed_read.rs"

if [[ ! -d "$REPO/.git" ]]; then
    printf '{"score": 0.0, "passed": false, "detail": "h2 workspace repo not found at %s"}\n' "$REPO"
    exit 0
fi

CHANGED="$(git -C "$REPO" diff --name-only HEAD 2>/dev/null)"

if printf '%s\n' "$CHANGED" | grep -Fxq "$TARGET"; then
    printf '{"score": 1.0, "passed": true, "detail": "diff modifies h2 HTTP/2 frame reader (real fix site)"}\n'
elif [[ -n "${CHANGED//[[:space:]]/}" ]]; then
    printf '{"score": 0.2, "passed": false, "detail": "h2 has a diff but it does not touch the frame-reader fix site"}\n'
else
    printf '{"score": 0.0, "passed": false, "detail": "no uncommitted change in h2"}\n'
fi
