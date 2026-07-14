#!/usr/bin/env bash
# check_patch_site.sh — the uncommitted diff must land in httpcore5's connection
# pool implementation (the real fix site), not only in httpclient5's manager.
set -euo pipefail

WORKSPACE="${WORKSPACE:-/workspace}"
REPO="$WORKSPACE/httpcomponents-core"
STRICT="httpcore5/src/main/java/org/apache/hc/core5/pool/StrictConnPool.java"
LAX="httpcore5/src/main/java/org/apache/hc/core5/pool/LaxConnPool.java"

if [[ ! -d "$REPO/.git" ]]; then
    printf '{"score": 0.0, "passed": false, "detail": "httpcomponents-core workspace repo not found at %s"}\n' "$REPO"
    exit 0
fi

CHANGED="$(git -C "$REPO" diff --name-only HEAD 2>/dev/null)"

if printf '%s\n' "$CHANGED" | grep -Fxq "$STRICT" || printf '%s\n' "$CHANGED" | grep -Fxq "$LAX"; then
    printf '{"score": 1.0, "passed": true, "detail": "diff modifies httpcore5 connection-pool implementation (real fix site)"}\n'
elif [[ -n "${CHANGED//[[:space:]]/}" ]]; then
    printf '{"score": 0.2, "passed": false, "detail": "httpcomponents-core has a diff but it does not touch the pool lease-future fix site"}\n'
else
    printf '{"score": 0.0, "passed": false, "detail": "no uncommitted change in httpcomponents-core"}\n'
fi
