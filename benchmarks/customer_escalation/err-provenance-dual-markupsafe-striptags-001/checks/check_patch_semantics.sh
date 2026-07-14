#!/usr/bin/env bash
# check_patch_semantics.sh — the patch must make the tag-strip regexes match across
# newlines: add re.DOTALL to the compilation, or switch to a newline-inclusive
# pattern like [\s\S]. Scoped to markupsafe's main module.
set -euo pipefail

WORKSPACE="${WORKSPACE:-/workspace}"
REPO="$WORKSPACE/markupsafe"

if [[ ! -d "$REPO/.git" ]]; then
    printf '{"score": 0.0, "passed": false, "detail": "markupsafe workspace repo not found"}\n'
    exit 0
fi

DIFF="$(git -C "$REPO" diff HEAD -- 'src/markupsafe/__init__.py' 2>/dev/null)"
ADDED="$(printf '%s' "$DIFF" | grep '^+' | grep -v '^+++' || true)"

if [[ -z "${ADDED//[[:space:]]/}" ]]; then
    printf '{"score": 0.0, "passed": false, "detail": "no added lines in markupsafe __init__.py"}\n'
    exit 0
fi

if printf '%s' "$ADDED" | grep -q "DOTALL"; then
    printf '{"score": 1.0, "passed": true, "detail": "patch adds re.DOTALL so the tag-strip regexes match across newlines"}\n'
elif printf '%s' "$ADDED" | grep -Eq '\[\\s\\S\]|\[\\S\\s\]|re\.S'; then
    printf '{"score": 1.0, "passed": true, "detail": "patch uses a newline-inclusive pattern so multi-line tags are matched"}\n'
elif printf '%s' "$ADDED" | grep -Eq '_strip_tags_re|_strip_comments_re|re\.compile'; then
    printf '{"score": 0.5, "passed": false, "detail": "patch edits the strip regexes but does not clearly make them cross newlines"}\n'
else
    printf '{"score": 0.0, "passed": false, "detail": "patch does not make the tag-strip regexes match across newlines"}\n'
fi
