#!/usr/bin/env bash
# check_patch_semantics.sh — the patch must catch the registry lookup failure and
# re-raise SerializerNotInstalled instead of letting a bare KeyError escape.
# Scoped to kombu's serialization module.
set -euo pipefail

WORKSPACE="${WORKSPACE:-/workspace}"
REPO="$WORKSPACE/kombu"

# ADDED/REMOVED carry the agent's diff with comments stripped, so a token
# inside a comment reads as absent: appending one comment line at the fix site
# used to score 1.0 here (bead jn73.2.7, D2).
ADDED="$(python3 -m eb_verify.plugins.patch_evidence code \
    --repo "$REPO" --path kombu/serialization.py --lang python --added)"

if [[ -z "${ADDED//[[:space:]]/}" ]]; then
    printf '{"score": 0.0, "passed": false, "detail": "no added lines in kombu serialization.py"}\n'
    exit 0
fi

if printf '%s' "$ADDED" | grep -q "SerializerNotInstalled"; then
    if printf '%s' "$ADDED" | grep -Eq "except KeyError|except .*KeyError"; then
        printf '{"score": 1.0, "passed": true, "detail": "patch catches KeyError and re-raises SerializerNotInstalled for the unregistered serializer"}\n'
    else
        printf '{"score": 0.6, "passed": true, "detail": "patch references SerializerNotInstalled; the KeyError guard is not clearly shown"}\n'
    fi
else
    printf '{"score": 0.0, "passed": false, "detail": "patch does not convert the bare KeyError into a not-installed serializer error"}\n'
fi
