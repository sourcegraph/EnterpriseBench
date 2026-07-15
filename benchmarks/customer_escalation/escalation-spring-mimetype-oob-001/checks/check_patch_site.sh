#!/usr/bin/env bash
# check_patch_site.sh — the uncommitted diff must land in spring-framework's spring-core mime-type text parser (the real fix site), not spring-boot's handler.
#
# Scored on the diff with comments stripped: appending a comment at the fix site
# is not a patch, and used to score 1.0 here (bead jn73.2.7, D2).
set -euo pipefail

WORKSPACE="${WORKSPACE:-/workspace}"

exec python3 -m eb_verify.plugins.patch_evidence site \
    --repo "$WORKSPACE/spring-framework" \
    --path spring-core/src/main/java/org/springframework/util/MimeTypeUtils.java \
    --lang java
