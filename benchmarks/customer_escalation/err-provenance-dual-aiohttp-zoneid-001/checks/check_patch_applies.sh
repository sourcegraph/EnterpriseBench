#!/usr/bin/env bash
# check_patch_applies.sh — a code patch must exist under the workspace and the
# change must actually address the %25 zone separator (not an empty or unrelated
# diff). Scoped to the yarl URL module where the fix belongs.
set -euo pipefail

WORKSPACE="${WORKSPACE:-/workspace}"
REPO="$WORKSPACE/yarl"

# The genuine fix decodes the %25-encoded zone separator via a replace/partition
# on a host VARIABLE. `colocated` marks string edges (\x01) so credit requires a
# `<word>.replace(`/`.partition(` call flush against the %25 literal —
# `host.replace('%25', '%')`, across lines or not. The `\w\s*\.` receiver refuses
# a dead `zone = "%25"` (no call), an unrelated `x.replace('foo', 'bar')` (marked
# string is not %25), and a self-replace on the literal itself
# (`('%25').replace('%25', '%')` — the receiver is `)`, not a word). What it
# cannot see is reachability (the call inside `if False:` or a never-called
# helper) — the pre-existing lexical floor, EnterpriseBench-jn73.2.7.5. Bead
# jn73.2.7, residual A and the co-location follow-up.
HITS="$(python3 -m eb_verify.plugins.patch_evidence colocated \
    --repo "$REPO" --path yarl/_url.py --lang python --added \
    --pattern '\w\s*\.\s*(?:replace|partition)\(\s*\x01[^\x01]*%25')"

if [[ "$HITS" -ge 1 ]]; then
    printf '{"score": 1.0, "passed": true, "detail": "patch handles the %%25 encoded zone separator in yarl/_url.py"}\n'
else
    printf '{"score": 0.0, "passed": false, "detail": "diff in yarl/_url.py does not decode the %%25 zone separator (a bare %%25 literal with no replace/partition operating on it is not a fix)"}\n'
fi
