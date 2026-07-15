#!/usr/bin/env bash
# check_patch_semantics.sh — the patch must make x-gzip decode as gzip: it should
# appear in the decoder dispatch and in the recognized-encodings list, not fall
# through to deflate. Scoped to urllib3's response module.
set -euo pipefail

WORKSPACE="${WORKSPACE:-/workspace}"
REPO="$WORKSPACE/urllib3"

# ADDED/REMOVED carry the agent's diff with comments stripped, so a token
# inside a comment reads as absent: appending one comment line at the fix site
# used to score 1.0 here (bead jn73.2.7, D2).
ADDED="$(python3 -m eb_verify.plugins.patch_evidence code \
    --repo "$REPO" --path src/urllib3/response.py --lang python --added)"

if [[ -z "${ADDED//[[:space:]]/}" ]]; then
    printf '{"score": 0.0, "passed": false, "detail": "no added lines in urllib3 response.py"}\n'
    exit 0
fi

# Count distinct added lines mentioning x-gzip: the correct fix touches both the
# decoder dispatch and the CONTENT_DECODERS list (two occurrences).
XGZIP_LINES="$(printf '%s' "$ADDED" | grep -c "x-gzip" || true)"

if [[ "$XGZIP_LINES" -ge 2 ]]; then
    printf '{"score": 1.0, "passed": true, "detail": "patch adds x-gzip to both the decoder dispatch and the recognized-encodings list"}\n'
elif [[ "$XGZIP_LINES" -eq 1 ]]; then
    printf '{"score": 0.6, "passed": true, "detail": "patch references x-gzip in one place; only a partial fix (dispatch or list, not both)"}\n'
else
    printf '{"score": 0.0, "passed": false, "detail": "patch in response.py does not route x-gzip to gzip decoding"}\n'
fi
