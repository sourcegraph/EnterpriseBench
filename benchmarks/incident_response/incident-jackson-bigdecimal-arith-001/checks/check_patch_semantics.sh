#!/usr/bin/env bash
# check_patch_semantics.sh — the patch must actually handle the arithmetic
# overflow (catch ArithmeticException / convert it to a number-format error),
# scoped to the parser rather than a blanket swallow.
set -euo pipefail

WORKSPACE="${WORKSPACE:-/workspace}"
REPO="$WORKSPACE/jackson-core"

if [[ ! -d "$REPO/.git" ]]; then
    printf '{"score": 0.0, "passed": false, "detail": "jackson-core workspace repo not found"}\n'
    exit 0
fi

# Only consider added lines in the big-decimal parser file.
DIFF="$(git -C "$REPO" diff HEAD -- 'src/main/java/com/fasterxml/jackson/core/io/BigDecimalParser.java' 2>/dev/null)"
ADDED="$(printf '%s' "$DIFF" | grep '^+' | grep -v '^+++' || true)"

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
