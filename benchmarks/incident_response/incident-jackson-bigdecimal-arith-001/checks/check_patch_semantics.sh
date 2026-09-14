#!/usr/bin/env bash
# check_patch_semantics.sh — the patch must actually handle the arithmetic
# overflow (catch ArithmeticException / convert it to a number-format error),
# scoped to the parser rather than a blanket swallow.
set -euo pipefail

WORKSPACE="${WORKSPACE:-/workspace}"
REPO="$WORKSPACE/jackson-core"

# Only consider added lines in the big-decimal parser file.
# ADDED/REMOVED carry the agent's diff with comments stripped, so a token
# inside a comment reads as absent: appending one comment line at the fix site
# used to score 1.0 here (bead jn73.2.7, D2).
ADDED="$(python3 -m eb_verify.plugins.patch_evidence code \
    --repo "$REPO" --path src/main/java/com/fasterxml/jackson/core/io/BigDecimalParser.java --lang java --added)"

if printf '%s' "$ADDED" | grep -q "ArithmeticException"; then
    # Reject a bare catch-all that swallows everything (Throwable/Exception with no rethrow as number error)
    if printf '%s' "$ADDED" | grep -Eq "NumberFormatException|reportInvalid|_badBigDecimal|invalid"; then
        printf '{"score": 1.0, "passed": true, "detail": "patch catches ArithmeticException and surfaces it as a number-format parse error"}\n'
    else
        printf '{"score": 0.6, "passed": true, "detail": "patch references ArithmeticException; conversion to a normal parse error not clearly shown"}\n'
    fi
else
    printf '{"score": 0.0, "passed": false, "detail": "patch does not handle ArithmeticException at the parser"}\n'
fi
