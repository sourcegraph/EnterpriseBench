#!/usr/bin/env bash
# check_patch_site.sh — the uncommitted diff must land in jackson-core's
# big-decimal text parser (the real fix site), not only in jackson-databind.
set -euo pipefail

WORKSPACE="${WORKSPACE:-/workspace}"
REPO="$WORKSPACE/jackson-core"
TARGET="src/main/java/com/fasterxml/jackson/core/io/BigDecimalParser.java"

if [[ ! -d "$REPO/.git" ]]; then
    printf '{"score": 0.0, "passed": false, "detail": "jackson-core workspace repo not found at %s"}\n' "$REPO"
    exit 0
fi

CHANGED="$(git -C "$REPO" diff --name-only HEAD 2>/dev/null)"

if printf '%s\n' "$CHANGED" | grep -Fxq "$TARGET"; then
    printf '{"score": 1.0, "passed": true, "detail": "diff modifies jackson-core BigDecimalParser.java (real fix site)"}\n'
elif [[ -n "${CHANGED//[[:space:]]/}" ]]; then
    printf '{"score": 0.2, "passed": false, "detail": "jackson-core has a diff but it does not touch the big-decimal parser fix site"}\n'
else
    printf '{"score": 0.0, "passed": false, "detail": "no uncommitted change in jackson-core"}\n'
fi
