#!/usr/bin/env bash
# check_patch_semantics.sh — the patch must correct the extra-field length parse:
# assign (not OR) so the -1 sentinel is overwritten, and combine the length bytes
# little-endian (low byte first). A bare OR or wrong order is not the fix.
set -euo pipefail

WORKSPACE="${WORKSPACE:-/workspace}"
REPO="$WORKSPACE/netty"

# ADDED/REMOVED carry the agent's diff with comments stripped, so a token
# inside a comment reads as absent: appending one comment line at the fix site
# used to score 1.0 here (bead jn73.2.7, D2).
ADDED="$(python3 -m eb_verify.plugins.patch_evidence code \
    --repo "$REPO" --path codec/src/main/java/io/netty/handler/codec/compression/JdkZlibDecoder.java --lang java --added)"

if [[ -z "${ADDED//[[:space:]]/}" ]]; then
    printf '{"score": 0.0, "passed": false, "detail": "no added lines in the gzip decoder"}\n'
    exit 0
fi

# Strong signal: an assignment that combines the low byte (xlen1) after shifting the
# high byte (xlen2), i.e. little-endian `xlen = xlen2 << 8 | xlen1` (or xlen1 | xlen2 << 8).
if printf '%s' "$ADDED" | grep -Eq "xlen[[:space:]]*=[^=]" \
    && printf '%s' "$ADDED" | grep -Eq "xlen2[[:space:]]*<<[[:space:]]*8[[:space:]]*\|[[:space:]]*xlen1|xlen1[[:space:]]*\|[[:space:]]*xlen2[[:space:]]*<<[[:space:]]*8"; then
    printf '{"score": 1.0, "passed": true, "detail": "patch assigns the little-endian XLEN (low byte first), overwriting the -1 sentinel"}\n'
elif printf '%s' "$ADDED" | grep -Eq "xlen[[:space:]]*=[^=]" && printf '%s' "$ADDED" | grep -Eq "xlen1|xlen2|<<[[:space:]]*8"; then
    printf '{"score": 0.6, "passed": true, "detail": "patch reworks the XLEN assignment; correct little-endian order not clearly shown"}\n'
else
    printf '{"score": 0.0, "passed": false, "detail": "patch does not correct the extra-field length parse (assign + little-endian)"}\n'
fi
