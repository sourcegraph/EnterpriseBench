#!/usr/bin/env bash
# check_patch_site.sh — the uncommitted diff must land in hyper's HTTP/1 chunked decoder (the real fix site), not only in reqwest.
#
# Scored on the diff with comments stripped: appending a comment at the fix site
# is not a patch, and used to score 1.0 here (bead jn73.2.7, D2).
set -euo pipefail

WORKSPACE="${WORKSPACE:-/workspace}"

exec python3 -m eb_verify.plugins.patch_evidence site \
    --repo "$WORKSPACE/hyper" \
    --path src/proto/h1/decode.rs \
    --lang rust
