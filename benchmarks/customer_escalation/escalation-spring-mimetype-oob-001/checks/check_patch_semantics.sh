#!/usr/bin/env bash
# check_patch_semantics.sh — the patch must guard the index before the
# backtracking read (charAt(nextIndex - 1)) so a leading quote no longer reads
# charAt(-1), converting the unchecked index error into the normal parse error.
set -euo pipefail

WORKSPACE="${WORKSPACE:-/workspace}"
REPO="$WORKSPACE/spring-framework"

if [[ ! -d "$REPO/.git" ]]; then
    printf '{"score": 0.0, "passed": false, "detail": "spring-framework workspace repo not found"}\n'
    exit 0
fi

DIFF="$(git -C "$REPO" diff HEAD -- 'spring-core/src/main/java/org/springframework/util/MimeTypeUtils.java' 2>/dev/null; git -C "$REPO" diff --cached -- 'spring-core/src/main/java/org/springframework/util/MimeTypeUtils.java' 2>/dev/null)"
ADDED="$(printf '%s' "$DIFF" | grep '^+' | grep -v '^+++' || true)"

if [[ -z "$(printf '%s' "$ADDED" | tr -d '[:space:]')" ]]; then
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
