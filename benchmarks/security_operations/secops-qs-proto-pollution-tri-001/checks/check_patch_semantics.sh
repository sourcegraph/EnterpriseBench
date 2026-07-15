#!/usr/bin/env bash
# check_patch_semantics.sh — the patch must exclude the __proto__ key from being
# assigned during recursive key expansion, scoped to the parse module rather than
# a blanket rewrite.
set -euo pipefail

WORKSPACE="${WORKSPACE:-/workspace}"
REPO="$WORKSPACE/qs"

# ADDED/REMOVED carry the agent's diff with comments stripped, so a token
# inside a comment reads as absent: appending one comment line at the fix site
# used to score 1.0 here (bead jn73.2.7, D2).
ADDED="$(python3 -m eb_verify.plugins.patch_evidence code \
    --repo "$REPO" --path lib/parse.js --lang javascript --added)"

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
