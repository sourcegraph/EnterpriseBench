#!/usr/bin/env bash
# check_patch_applies.sh — a code patch must exist under the workspace and the
# change must actually address the %25 zone separator (not an empty or unrelated
# diff). Scoped to the yarl URL module where the fix belongs.
set -euo pipefail

WORKSPACE="${WORKSPACE:-/workspace}"
REPO="$WORKSPACE/yarl"

# ADDED/REMOVED carry the agent's diff with comments stripped, so a token
# inside a comment reads as absent: appending one comment line at the fix site
# used to score 1.0 here (bead jn73.2.7, D2).
ADDED="$(python3 -m eb_verify.plugins.patch_evidence code \
    --repo "$REPO" --path yarl/_url.py --lang python --added)"

if [[ -z "${ADDED//[[:space:]]/}" ]]; then
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
