#!/usr/bin/env bash
# check_patch_semantics.sh — the patch must count CONTINUATION frames per header
# block and fail the connection once a bound is exceeded, rather than reassemble
# without limit.
set -euo pipefail

WORKSPACE="${WORKSPACE:-/workspace}"
REPO="$WORKSPACE/h2"

# ADDED/REMOVED carry the agent's diff with comments stripped, so a token
# inside a comment reads as absent: appending one comment line at the fix site
# used to score 1.0 here (bead jn73.2.7, D2).
ADDED="$(python3 -m eb_verify.plugins.patch_evidence code \
    --repo "$REPO" --path src/codec/framed_read.rs --lang rust --added)"

# A bound on CONTINUATION frames: a max/limit and a per-block count.
if printf '%s' "$ADDED" | grep -Eiq "continuation" && printf '%s' "$ADDED" | grep -Eq "max_|_max|limit|count|>=|>"; then
    # And the overflow fails the connection rather than being ignored.
    if printf '%s' "$ADDED" | grep -Eq "Err\(|go_away|GoAway|GOAWAY|Reason|library_go_away|PROTOCOL_ERROR|ENHANCE_YOUR_CALM"; then
        printf '{"score": 1.0, "passed": true, "detail": "patch bounds CONTINUATION frames and fails the connection on overflow"}\n'
    else
        printf '{"score": 0.6, "passed": true, "detail": "patch bounds CONTINUATION frames but the connection-failure path is not clearly shown"}\n'
    fi
else
    printf '{"score": 0.0, "passed": false, "detail": "patch does not add a CONTINUATION-frame bound to the reassembly"}\n'
fi
