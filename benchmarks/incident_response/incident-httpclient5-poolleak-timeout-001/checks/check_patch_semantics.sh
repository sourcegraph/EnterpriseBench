#!/usr/bin/env bash
# check_patch_semantics.sh — the patch must make the timeout rethrow conditional
# on the cancel result and recover the delivered entry (super.get()) when the
# cancel loses the race, rather than unconditionally rethrowing and leaking.
set -euo pipefail

WORKSPACE="${WORKSPACE:-/workspace}"
REPO="$WORKSPACE/httpcomponents-core"

if [[ ! -d "$REPO/.git" ]]; then
    printf '{"score": 0.0, "passed": false, "detail": "httpcomponents-core workspace repo not found"}\n'
    exit 0
fi

DIFF="$(git -C "$REPO" diff HEAD -- 'httpcore5/src/main/java/org/apache/hc/core5/pool/StrictConnPool.java' 'httpcore5/src/main/java/org/apache/hc/core5/pool/LaxConnPool.java' 2>/dev/null)"
ADDED="$(printf '%s' "$DIFF" | grep '^+' | grep -v '^+++' || true)"

if [[ -z "${ADDED//[[:space:]]/}" ]]; then
    printf '{"score": 0.0, "passed": false, "detail": "no added lines in the connection-pool lease future"}\n'
    exit 0
fi

# Fix makes the rethrow conditional on cancel() winning, AND recovers the delivered entry.
if printf '%s' "$ADDED" | grep -Eq "if[[:space:]]*\([[:space:]]*cancel\(\)" \
    && printf '%s' "$ADDED" | grep -Eq "super\.get\(\)|return[[:space:]]+super\.get"; then
    printf '{"score": 1.0, "passed": true, "detail": "patch rethrows only when cancel() wins and recovers the delivered entry via super.get()"}\n'
elif printf '%s' "$ADDED" | grep -Eq "cancel\(\)" \
    && printf '%s' "$ADDED" | grep -Eq "super\.get|return"; then
    printf '{"score": 0.6, "passed": true, "detail": "patch references cancel() and an entry recovery path; conditional-rethrow shape not clearly shown"}\n'
else
    printf '{"score": 0.0, "passed": false, "detail": "patch does not make the timeout rethrow conditional on cancel() and recover the delivered entry"}\n'
fi
