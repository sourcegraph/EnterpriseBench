#!/usr/bin/env bash
# check_patch_site.sh — the uncommitted diff must land in netty's gzip decoder
# (the real fix site), not only in reactor-netty.
set -euo pipefail

WORKSPACE="${WORKSPACE:-/workspace}"
REPO="$WORKSPACE/netty"
TARGET="codec/src/main/java/io/netty/handler/codec/compression/JdkZlibDecoder.java"

if [[ ! -d "$REPO/.git" ]]; then
    printf '{"score": 0.0, "passed": false, "detail": "netty workspace repo not found at %s"}\n' "$REPO"
    exit 0
fi

CHANGED="$(git -C "$REPO" diff --name-only HEAD 2>/dev/null; git -C "$REPO" diff --cached --name-only 2>/dev/null)"

if printf '%s\n' "$CHANGED" | grep -Fxq "$TARGET"; then
    printf '{"score": 1.0, "passed": true, "detail": "diff modifies netty JdkZlibDecoder.java (real fix site)"}\n'
elif [[ -n "$(printf '%s' "$CHANGED" | tr -d '[:space:]')" ]]; then
    printf '{"score": 0.2, "passed": false, "detail": "netty has a diff but it does not touch the gzip decoder fix site"}\n'
else
    printf '{"score": 0.0, "passed": false, "detail": "no uncommitted change in netty"}\n'
fi
