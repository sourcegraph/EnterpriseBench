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
# `colocated` marks string edges (\x01) and emits only the agent's ADDED lines.
# Three things are required per site, each closing a family of non-fixes rather
# than a spelling:
#   * the real machinery's name plus a structural verb — `mode|content_encoding
#     in`, or a `CONTENT_DECODERS =` assignment anchored at the START of a line.
#     A name alone is not an operation, and `dict(CONTENT_DECODERS=[...])` is a
#     keyword argument, not the registry.
#   * BOTH "gzip" AND "x-gzip" as marked elements, with the window excluding
#     EVERY bracket. It therefore cannot descend into a nested collection
#     (`mode in (["gzip", "x-gzip"],)` tests membership against a 1-tuple holding
#     a list: always False) nor climb out into a neighbouring one.
#   * the collection must CLOSE within the added text. Only added lines are
#     emitted, so a closer left on an unchanged CONTEXT line is invisible and the
#     window would run on into a later statement, pooling their elements
#     (`CONTENT_DECODERS = ["gzip"` + a far-away `x = a, "x-gzip"`). Requiring the
#     closer costs a genuine fix nothing: BOTH real sites are single-line at
#     2.0.7, so a real fix rewrites the line and carries its own bracket.
# Together these refuse the dead reassignment `CONTENT_DECODERS = ("x-gzip",)`
# (no gzip element — a broken 1-tuple REPLACES the registry rather than adding to
# it), a dead tuple under the wrong name, a scalar `content_encoding = "x-gzip"`
# (no verb), and a substring "noise-x-gzip-noise" (not the exact element).
# What it cannot see is reachability (the list inside a never-called def) — the
# pre-existing lexical floor, EnterpriseBench-jn73.2.7.5. Bead jn73.2.7,
# residual A, the co-location follow-up, and defect B.
BOTH_ELEMENTS='(?=[^)\]([]{0,200}?\x01gzip\x01)(?=[^)\]([]{0,200}?\x01x-gzip\x01)[^)\]([]{0,200}?[)\]]'

hits() {
    python3 -m eb_verify.plugins.patch_evidence colocated \
        --repo "$REPO" --path src/urllib3/response.py --lang python --added \
        --pattern "$1"
}

DISPATCH="$(hits '(?:mode|content_encoding)\s+in\s*[\[(]'"$BOTH_ELEMENTS")"
REGISTRY="$(hits '(?m:^)\s*(?:[\w.]+\.)?CONTENT_DECODERS\s*\+?=\s*[\[(]'"$BOTH_ELEMENTS")"

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
