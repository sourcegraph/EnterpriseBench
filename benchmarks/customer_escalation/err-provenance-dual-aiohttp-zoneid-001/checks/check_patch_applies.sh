#!/usr/bin/env bash
# check_patch_applies.sh — a code patch must exist under the workspace and the
# change must actually address the %25 zone separator (not an empty or unrelated
# diff). Scoped to the yarl URL module where the fix belongs.
set -euo pipefail

WORKSPACE="${WORKSPACE:-/workspace}"
REPO="$WORKSPACE/yarl"

if [[ ! -d "$REPO/.git" ]]; then
    printf '{"score": 0.0, "passed": false, "detail": "yarl workspace repo not found"}\n'
    exit 0
fi

DIFF="$(git -C "$REPO" diff HEAD -- 'yarl/_url.py' 2>/dev/null; git -C "$REPO" diff --cached -- 'yarl/_url.py' 2>/dev/null)"
ADDED="$(printf '%s' "$DIFF" | grep '^+' | grep -v '^+++' || true)"

if [[ -z "$(printf '%s' "$ADDED" | tr -d '[:space:]')" ]]; then
    printf '{"score": 0.0, "passed": false, "detail": "no added lines in yarl/_url.py"}\n'
    exit 0
fi

# The correct fix references the RFC-encoded zone separator %25 (decode to %,
# or partition on %25). Accept either the literal token or a clear zone/replace.
if printf '%s' "$ADDED" | grep -q "%25"; then
    printf '{"score": 1.0, "passed": true, "detail": "patch handles the %%25 encoded zone separator in yarl/_url.py"}\n'
elif printf '%s' "$ADDED" | grep -Eq "zone|partition|replace\("; then
    printf '{"score": 0.6, "passed": true, "detail": "patch edits the host/zone handling but does not clearly reference the %%25 separator"}\n'
else
    printf '{"score": 0.2, "passed": false, "detail": "diff in yarl/_url.py does not address the zone separator"}\n'
fi
