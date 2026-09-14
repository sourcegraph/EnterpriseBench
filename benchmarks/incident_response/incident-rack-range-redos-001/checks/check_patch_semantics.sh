#!/usr/bin/env bash
# check_patch_semantics.sh — the patch must remove the catastrophic backtracking
# from the byte-range parse, replacing it with a non-backtracking (split-based)
# parse, scoped to the utility rather than an app-layer cap.
set -euo pipefail

WORKSPACE="${WORKSPACE:-/workspace}"
REPO="$WORKSPACE/rack"

# ADDED/REMOVED carry the agent's diff with comments stripped, so a token
# inside a comment reads as absent: appending one comment line at the fix site
# used to score 1.0 here (bead jn73.2.7, D2).
ADDED="$(python3 -m eb_verify.plugins.patch_evidence code \
    --repo "$REPO" --path lib/rack/utils.rb --lang ruby --added)"
REMOVED="$(python3 -m eb_verify.plugins.patch_evidence code \
    --repo "$REPO" --path lib/rack/utils.rb --lang ruby --removed)"

# A linear replacement uses split/include? on the range segment.
if printf '%s' "$ADDED" | grep -Eq "\.split\(|\.include\?\(|split\('-'\)|include\?\('-'\)"; then
    printf '{"score": 1.0, "passed": true, "detail": "patch replaces the backtracking range parse with a split-based linear parse"}\n'
elif printf '%s' "$REMOVED" | grep -Eq '\(\\d\*\)-\(\\d\*\)|range_spec =~'; then
    # Removed the nested-quantifier regex but linear replacement not clearly shown.
    printf '{"score": 0.6, "passed": true, "detail": "patch removes the backtracking regex; linear replacement not clearly shown"}\n'
else
    printf '{"score": 0.0, "passed": false, "detail": "patch does not remove the catastrophic backtracking at the byte-range parser"}\n'
fi
