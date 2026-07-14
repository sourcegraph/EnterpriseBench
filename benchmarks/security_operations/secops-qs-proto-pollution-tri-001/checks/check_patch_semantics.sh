#!/usr/bin/env bash
# check_patch_semantics.sh — the patch must exclude the __proto__ key from being
# assigned during recursive key expansion, scoped to the parse module rather than
# a blanket rewrite.
set -euo pipefail

WORKSPACE="${WORKSPACE:-/workspace}"
REPO="$WORKSPACE/qs"

if [[ ! -d "$REPO/.git" ]]; then
    printf '{"score": 0.0, "passed": false, "detail": "qs workspace repo not found"}\n'
    exit 0
fi

DIFF="$(git -C "$REPO" diff HEAD -- 'lib/parse.js' 2>/dev/null; git -C "$REPO" diff --cached -- 'lib/parse.js' 2>/dev/null)"
ADDED="$(printf '%s' "$DIFF" | grep '^+' | grep -v '^+++' || true)"

if printf '%s' "$ADDED" | grep -q "__proto__"; then
    # Must guard/skip the assignment, not just mention the token in a comment.
    if printf '%s' "$ADDED" | grep -Eq "!==|!=|continue|skip|prototype|indexOf|includes\(|=== '__proto__'|=== \"__proto__\""; then
        printf '{"score": 1.0, "passed": true, "detail": "patch excludes the __proto__ key during key expansion"}\n'
    else
        printf '{"score": 0.6, "passed": true, "detail": "patch references __proto__ but exclusion of the assignment is not clearly shown"}\n'
    fi
else
    printf '{"score": 0.0, "passed": false, "detail": "patch does not exclude the __proto__ key at the recursive parse routine"}\n'
fi
