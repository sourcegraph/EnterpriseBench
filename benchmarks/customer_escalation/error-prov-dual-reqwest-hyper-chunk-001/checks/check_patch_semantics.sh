#!/usr/bin/env bash
# check_patch_semantics.sh — the patch must replace the unchecked chunk-size
# accumulation with checked arithmetic that turns an overflowing size into a
# normal invalid-chunk framing error, not a silent saturate.
set -euo pipefail

WORKSPACE="${WORKSPACE:-/workspace}"
REPO="$WORKSPACE/hyper"

# ADDED/REMOVED carry the agent's diff with comments stripped, so a token
# inside a comment reads as absent: appending one comment line at the fix site
# used to score 1.0 here (bead jn73.2.7, D2).
ADDED="$(python3 -m eb_verify.plugins.patch_evidence code \
    --repo "$REPO" --path src/proto/h1/decode.rs --lang rust --added)"

# Overflow must be detected: checked arithmetic or an explicit overflow test.
if printf '%s' "$ADDED" | grep -Eq "checked_mul|checked_add|overflowing_mul|overflowing_add|checked_shl|overflow"; then
    # And converted into a real framing error rather than saturated silently.
    if printf '%s' "$ADDED" | grep -Eq "Err\(|io::Error|InvalidData|InvalidInput|Reason|return Poll::Ready\(Err"; then
        printf '{"score": 1.0, "passed": true, "detail": "patch uses checked arithmetic and surfaces overflow as an invalid-chunk error"}\n'
    else
        printf '{"score": 0.6, "passed": true, "detail": "patch guards the overflow but conversion to a framing error is not clearly shown"}\n'
    fi
else
    printf '{"score": 0.0, "passed": false, "detail": "patch does not add an overflow guard to the chunk-size accumulator"}\n'
fi
