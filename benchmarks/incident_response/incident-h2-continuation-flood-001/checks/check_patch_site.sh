#!/usr/bin/env bash
# check_patch_site.sh — the uncommitted diff must land in h2's HTTP/2 frame reader (the real fix site), not only in hyper.
#
# Scored on the diff with comments stripped: appending a comment at the fix site
# is not a patch, and used to score 1.0 here (bead jn73.2.7, D2).
set -euo pipefail

WORKSPACE="${WORKSPACE:-/workspace}"

exec python3 -m eb_verify.plugins.patch_evidence site \
    --repo "$WORKSPACE/h2" \
    --path src/codec/framed_read.rs \
    --lang rust
