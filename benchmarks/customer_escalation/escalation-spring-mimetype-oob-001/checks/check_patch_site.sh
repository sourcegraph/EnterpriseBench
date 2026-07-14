#!/usr/bin/env bash
# check_patch_site.sh — the uncommitted diff must land in spring-framework's
# spring-core mime-type text parser (the real fix site), not spring-boot's handler.
set -euo pipefail

WORKSPACE="${WORKSPACE:-/workspace}"
REPO="$WORKSPACE/spring-framework"
TARGET="spring-core/src/main/java/org/springframework/util/MimeTypeUtils.java"

if [[ ! -d "$REPO/.git" ]]; then
    printf '{"score": 0.0, "passed": false, "detail": "spring-framework workspace repo not found at %s"}\n' "$REPO"
    exit 0
fi

CHANGED="$(git -C "$REPO" diff --name-only HEAD 2>/dev/null; git -C "$REPO" diff --cached --name-only 2>/dev/null)"

if printf '%s\n' "$CHANGED" | grep -Fxq "$TARGET"; then
    printf '{"score": 1.0, "passed": true, "detail": "diff modifies spring-core MimeTypeUtils.java (real fix site)"}\n'
elif [[ -n "$(printf '%s' "$CHANGED" | tr -d '[:space:]')" ]]; then
    printf '{"score": 0.2, "passed": false, "detail": "spring-framework has a diff but it does not touch the mime-type parser fix site"}\n'
else
    printf '{"score": 0.0, "passed": false, "detail": "no uncommitted change in spring-framework"}\n'
fi
