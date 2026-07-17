#!/usr/bin/env bash
# check_patch_semantics.sh — the patch must make x-gzip decode as gzip: it should
# appear in the decoder dispatch and in the recognized-encodings list, not fall
# through to deflate. Scoped to urllib3's response module.
set -euo pipefail

WORKSPACE="${WORKSPACE:-/workspace}"
REPO="$WORKSPACE/urllib3"

# The genuine fix (upstream 5fc48e711b) widens two single-line statements in
# response.py, ADDING "x-gzip" alongside the "gzip" already there:
#   _get_decoder:          if mode == "gzip":        -> if mode in ("gzip", "x-gzip"):
#   BaseHTTPResponse:      CONTENT_DECODERS = ["gzip", "deflate"]
#                                                    -> [..., "x-gzip", ...]
# Both are needed — x-gzip in the list alone still dispatches to deflate, and a
# widened dispatch alone is never reached — so each site scores half.
#
# `colocated` marks string edges (\x01). Two things are required per site, and
# both are what the added-alongside shape gives us for free:
#   * the real machinery's name plus a structural verb (`mode|content_encoding
#     in`, or a `CONTENT_DECODERS =` registry assignment) — a name alone is not
#     an operation;
#   * BOTH "gzip" AND "x-gzip" as exact marked elements of the SAME collection.
# The second is what refuses the dead reassignment `CONTENT_DECODERS =
# ("x-gzip",)` (real name, x-gzip in a bracket, no gzip element, broken 1-tuple)
# that an x-gzip-only anchor paid 1.0 for. `[^)\]]` holds each lookahead inside
# the one collection, so two unrelated statements cannot pool their elements.
# Still refused: a dead tuple under the wrong name, a scalar `content_encoding =
# "x-gzip"` (no verb), a substring "noise-x-gzip-noise" (not the exact element).
# What it cannot see is reachability (the list inside a never-called def) — the
# pre-existing lexical floor, EnterpriseBench-jn73.2.7.5. Bead jn73.2.7,
# residual A, the co-location follow-up, and defect B.
BOTH_ELEMENTS='(?=[^)\]]{0,200}?[\[(,]\s*\x01gzip\x01)(?=[^)\]]{0,200}?[\[(,]\s*\x01x-gzip\x01)'

hits() {
    python3 -m eb_verify.plugins.patch_evidence colocated \
        --repo "$REPO" --path src/urllib3/response.py --lang python --added \
        --pattern "$1"
}

DISPATCH="$(hits '(?:mode|content_encoding)\s+in\s*(?=[\[(])'"$BOTH_ELEMENTS")"
REGISTRY="$(hits 'CONTENT_DECODERS\s*\+?=\s*(?=[\[(])'"$BOTH_ELEMENTS")"

SITES=0
if [[ "$DISPATCH" -ge 1 ]]; then SITES=$((SITES + 1)); fi
if [[ "$REGISTRY" -ge 1 ]]; then SITES=$((SITES + 1)); fi

if [[ "$SITES" -ge 2 ]]; then
    printf '{"score": 1.0, "passed": true, "detail": "patch adds x-gzip alongside gzip in both the decoder dispatch and the recognized-encodings list"}\n'
elif [[ "$SITES" -eq 1 ]]; then
    printf '{"score": 0.6, "passed": true, "detail": "patch routes x-gzip at one site; only a partial fix (dispatch or decoder list, not both), so the body still falls through undecoded"}\n'
else
    printf '{"score": 0.0, "passed": false, "detail": "patch in response.py does not route x-gzip to gzip decoding (a bare x-gzip literal, or one replacing rather than joining gzip in a decoder collection, is not a fix)"}\n'
fi
