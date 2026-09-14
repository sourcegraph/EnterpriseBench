#!/usr/bin/env bash
# check_patch_site.sh — the uncommitted diff must land in follow-redirects' entry module (the real stripping site), not only in axios.
#
# Scored on the diff with comments stripped: appending a comment at the fix site
# is not a patch, and used to score 1.0 here (bead jn73.2.7, D2).
set -euo pipefail

WORKSPACE="${WORKSPACE:-/workspace}"

exec python3 -m eb_verify.plugins.patch_evidence site \
    --repo "$WORKSPACE/follow-redirects" \
    --path index.js \
    --lang javascript
