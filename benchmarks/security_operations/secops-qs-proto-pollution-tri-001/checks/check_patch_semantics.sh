#!/usr/bin/env bash
# check_patch_semantics.sh — the patch must exclude the __proto__ key from being
# assigned during recursive key expansion, scoped to the parse module rather than
# a blanket rewrite.
set -euo pipefail

WORKSPACE="${WORKSPACE:-/workspace}"
REPO="$WORKSPACE/qs"

# The genuine fix GUARDS on the key being the '__proto__' STRING literal. The
# `colocated` mode marks string edges (\x01); the pattern requires a
# comparison/membership token flush against that literal AND inside an `if (...)`
# condition — `if (key === '__proto__')`, wrapped across lines or not. That
# refuses a dead `obj.__proto__` (no marked string), a keyword merely sharing the
# line (nothing flush against the mark), and a free-standing or body comparison
# whose result gates nothing (`key === '__proto__'`, `x = (key === '__proto__')`).
# What it cannot see is reachability: the guard wrapped in `if (false)` or a
# never-called function still reads as code — the pre-existing lexical floor
# tracked in EnterpriseBench-jn73.2.7.5. Bead jn73.2.7, residual A and the
# co-location follow-up.
HITS="$(python3 -m eb_verify.plugins.patch_evidence colocated \
    --repo "$REPO" --path lib/parse.js --lang javascript --added \
    --pattern 'if\s*\([^)]*(?:===|!==|!=|indexOf\(|includes\()[^)]*\x01[^\x01]*__proto__')"

if [[ "$HITS" -ge 1 ]]; then
    printf '{"score": 1.0, "passed": true, "detail": "patch guards the __proto__ key during key expansion"}\n'
else
    printf '{"score": 0.0, "passed": false, "detail": "patch does not guard the __proto__ key at the recursive parse routine (a bare __proto__ with no comparison against the string literal is not a guard)"}\n'
fi
