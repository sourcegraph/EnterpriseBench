#!/usr/bin/env bash
# check_patch_site.sh — the uncommitted diff must land in yarl's URL module (yarl/_url.py, the real fix site for the zone-id decoding), not only in aiohttp.
#
# Scored on the diff with comments stripped: appending a comment at the fix site
# is not a patch, and used to score 1.0 here (bead jn73.2.7, D2).
set -euo pipefail

WORKSPACE="${WORKSPACE:-/workspace}"

exec python3 -m eb_verify.plugins.patch_evidence site \
    --repo "$WORKSPACE/yarl" \
    --path yarl/_url.py \
    --lang python
