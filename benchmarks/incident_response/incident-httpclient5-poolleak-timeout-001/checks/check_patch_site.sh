#!/usr/bin/env bash
# check_patch_site.sh — the uncommitted diff must land in httpcore5's connection pool implementation (the real fix site), not only in httpclient5's manager.
#
# Scored on the diff with comments stripped: appending a comment at the fix site
# is not a patch, and used to score 1.0 here (bead jn73.2.7, D2).
set -euo pipefail

WORKSPACE="${WORKSPACE:-/workspace}"

exec python3 -m eb_verify.plugins.patch_evidence site \
    --repo "$WORKSPACE/httpcomponents-core" \
    --path httpcore5/src/main/java/org/apache/hc/core5/pool/StrictConnPool.java \
    --path httpcore5/src/main/java/org/apache/hc/core5/pool/LaxConnPool.java \
    --lang java
