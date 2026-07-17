#!/usr/bin/env bash
# check_patch_semantics.sh — the patch must make x-gzip decode as gzip: it should
# appear in the decoder dispatch and in the recognized-encodings list, not fall
# through to deflate. Scoped to urllib3's response module.
set -euo pipefail

WORKSPACE="${WORKSPACE:-/workspace}"
REPO="$WORKSPACE/urllib3"

# The genuine fix adds the exact "x-gzip" STRING as a *list/tuple element* at two
# sites, each naming the real decoder machinery: the encoding check
# `content_encoding in (..., "x-gzip")` and `CONTENT_DECODERS = [..., "x-gzip"]`.
# `colocated` marks string edges (\x01); the pattern requires the real name
# (content_encoding/CONTENT_DECODERS) within the statement, then the exact marked
# `\x01x-gzip\x01` in a list/tuple position (preceded by `(`, `[` or `,`), across
# lines or not, and counts the two sites. That refuses a dead
# `decoder = "x-gzip"` (scalar, wrong name), a dead `x = ('gzip', 'x-gzip')`
# (wrong name), and a substring `"noise-x-gzip"` (not the exact element). What it
# cannot see is reachability (the list inside a never-called def) — the
# pre-existing lexical floor, EnterpriseBench-jn73.2.7.5. Bead jn73.2.7, residual
# A and the co-location follow-up.
HITS="$(python3 -m eb_verify.plugins.patch_evidence colocated \
    --repo "$REPO" --path src/urllib3/response.py --lang python --added \
    --pattern '(?:content_encoding|CONTENT_DECODERS).{0,120}?[\[(,]\s*\x01x-gzip\x01')"

if [[ "$HITS" -ge 2 ]]; then
    printf '{"score": 1.0, "passed": true, "detail": "patch adds x-gzip to both the decoder dispatch and the recognized-encodings list"}\n'
elif [[ "$HITS" -eq 1 ]]; then
    printf '{"score": 0.6, "passed": true, "detail": "patch routes x-gzip in one list/tuple site; only a partial fix (dispatch or list, not both)"}\n'
else
    printf '{"score": 0.0, "passed": false, "detail": "patch in response.py does not route x-gzip to gzip decoding (a bare x-gzip literal outside a decoder list is not a fix)"}\n'
fi
