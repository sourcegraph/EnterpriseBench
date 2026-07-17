#!/usr/bin/env bash
# check_patch_semantics.sh — the patch must make the tag-strip regexes match across
# newlines: add re.DOTALL to the compilation, or switch to a newline-inclusive
# pattern like [\s\S]. Scoped to markupsafe's main module.
set -euo pipefail

WORKSPACE="${WORKSPACE:-/workspace}"
REPO="$WORKSPACE/markupsafe"
NAME='(?:_strip_tags_re|_strip_comments_re)\s*=\s*re\.compile\('

colocated() {
    python3 -m eb_verify.plugins.patch_evidence colocated \
        --repo "$REPO" --path src/markupsafe/__init__.py --lang python --added --pattern "$1"
}

# The fix recompiles the REAL strip regex (_strip_tags_re/_strip_comments_re) so
# it crosses newlines: with re.DOTALL/re.S (bare tokens after the pattern arg),
# or a newline-inclusive char class like [\s\S] (a string-interior token). Both
# full-credit patterns require the compile argument to be a COMPLETE marked
# string (\x01...\x01) — two marks — which a dead assignment whose whole VALUE is
# one outer string cannot supply (it has only the one outer pair, with the name
# and compile call as inert text between them). So `DUMMY = "_strip_tags_re =
# re.compile(pattern, re.DOTALL)"` scores 0. Bead jn73.2.7, residual A and the
# co-location follow-up. Single-quoted so python sees the patterns verbatim.
DOTALL="$(colocated "${NAME}"'[^\x01]*\x01[^\x01]*\x01[^)]*(?:re\.DOTALL|\bDOTALL\b|\bre\.S\b)')"
CHARCLASS="$(colocated "${NAME}"'[^\x01]*\x01[^\x01]*(?:\[\\s\\S\]|\[\\S\\s\])[^\x01]*\x01')"

# Partial: the real regex recompiled at all, but no clear newline change. Read on
# the BARE view (strings emptied), where a dead string's name vanishes, so only a
# real `_strip_tags_re = re.compile(` in live code reaches this tier.
EDITED="$(python3 -m eb_verify.plugins.patch_evidence code \
    --repo "$REPO" --path src/markupsafe/__init__.py --lang python --added \
    | grep -Ec '(_strip_tags_re|_strip_comments_re)[[:space:]]*=[[:space:]]*re\.compile\(' || true)"

if [[ "$DOTALL" -ge 1 || "$CHARCLASS" -ge 1 ]]; then
    printf '{"score": 1.0, "passed": true, "detail": "patch recompiles the strip regex so the tag-strip regexes match across newlines"}\n'
elif [[ "$EDITED" -ge 1 ]]; then
    printf '{"score": 0.5, "passed": false, "detail": "patch recompiles the strip regex but does not clearly make it cross newlines"}\n'
else
    printf '{"score": 0.0, "passed": false, "detail": "patch does not make the tag-strip regexes match across newlines"}\n'
fi
