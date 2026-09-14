#!/usr/bin/env bash
# check_patch_semantics.sh — the patch must guard the index before the
# backtracking read (charAt(nextIndex - 1)) so a leading quote no longer reads
# charAt(-1), converting the unchecked index error into the normal parse error.
set -euo pipefail

WORKSPACE="${WORKSPACE:-/workspace}"
REPO="$WORKSPACE/spring-framework"

# ADDED/REMOVED carry the agent's diff with comments stripped, so a token
# inside a comment reads as absent: appending one comment line at the fix site
# used to score 1.0 here (bead jn73.2.7, D2).
ADDED="$(python3 -m eb_verify.plugins.patch_evidence code \
    --repo "$REPO" --path spring-core/src/main/java/org/springframework/util/MimeTypeUtils.java --lang java --added)"

if [[ -z "${ADDED//[[:space:]]/}" ]]; then
    printf '{"score": 0.0, "passed": false, "detail": "no added lines in the mime-type parser"}\n'
    exit 0
fi

# Fix guards nextIndex before charAt(nextIndex - 1): `nextIndex == 0 ||` or `nextIndex > 0 &&`
# (or an equivalent explicit bounds check on nextIndex).
if printf '%s' "$ADDED" | grep -Eq "nextIndex[[:space:]]*==[[:space:]]*0" \
    || printf '%s' "$ADDED" | grep -Eq "nextIndex[[:space:]]*>[[:space:]]*0" \
    || printf '%s' "$ADDED" | grep -Eq "nextIndex[[:space:]]*>=[[:space:]]*1"; then
    printf '{"score": 1.0, "passed": true, "detail": "patch guards nextIndex before the backtracking charAt, eliminating the charAt(-1) read"}\n'
elif printf '%s' "$ADDED" | grep -Eq "nextIndex" && printf '%s' "$ADDED" | grep -Eq "charAt"; then
    printf '{"score": 0.6, "passed": true, "detail": "patch touches the nextIndex/charAt quote check; explicit bounds guard not clearly shown"}\n'
else
    printf '{"score": 0.0, "passed": false, "detail": "patch does not guard the index before the backtracking character read"}\n'
fi
