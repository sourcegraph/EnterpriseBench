#!/usr/bin/env bash
# check_patch_semantics.sh — the patch must make the tag-strip regexes match across
# newlines: add re.DOTALL to the compilation, or switch to a newline-inclusive
# pattern like [\s\S]. Scoped to markupsafe's main module.
set -euo pipefail

WORKSPACE="${WORKSPACE:-/workspace}"
REPO="$WORKSPACE/markupsafe"

# ADDED/REMOVED carry the agent's diff with comments stripped, so a token
# inside a comment reads as absent: appending one comment line at the fix site
# used to score 1.0 here (bead jn73.2.7, D2).
ADDED="$(python3 -m eb_verify.plugins.patch_evidence code \
    --repo "$REPO" --path src/markupsafe/__init__.py --lang python --added)"

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
