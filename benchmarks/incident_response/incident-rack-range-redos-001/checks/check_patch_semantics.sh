#!/usr/bin/env bash
# check_patch_semantics.sh — the patch must remove the catastrophic backtracking
# from the byte-range parse, replacing it with a non-backtracking (split-based)
# parse, scoped to the utility rather than an app-layer cap.
set -euo pipefail

WORKSPACE="${WORKSPACE:-/workspace}"
REPO="$WORKSPACE/rack"

if [[ ! -d "$REPO/.git" ]]; then
    printf '{"score": 0.0, "passed": false, "detail": "rack workspace repo not found"}\n'
    exit 0
fi

DIFF="$(git -C "$REPO" diff HEAD -- 'lib/rack/utils.rb' 2>/dev/null; git -C "$REPO" diff --cached -- 'lib/rack/utils.rb' 2>/dev/null)"
ADDED="$(printf '%s' "$DIFF" | grep '^+' | grep -v '^+++' || true)"
REMOVED="$(printf '%s' "$DIFF" | grep '^-' | grep -v '^---' || true)"

# A linear replacement uses split/include? on the range segment.
if printf '%s' "$ADDED" | grep -Eq "\.split\(|\.include\?\(|split\('-'\)|include\?\('-'\)"; then
    printf '{"score": 1.0, "passed": true, "detail": "patch replaces the backtracking range parse with a split-based linear parse"}\n'
elif printf '%s' "$REMOVED" | grep -Eq '\(\\d\*\)-\(\\d\*\)|range_spec =~'; then
    # Removed the nested-quantifier regex but linear replacement not clearly shown.
    printf '{"score": 0.6, "passed": true, "detail": "patch removes the backtracking regex; linear replacement not clearly shown"}\n'
else
    printf '{"score": 0.0, "passed": false, "detail": "patch does not remove the catastrophic backtracking at the byte-range parser"}\n'
fi
