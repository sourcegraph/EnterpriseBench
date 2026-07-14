#!/usr/bin/env bash
# check_patch_semantics.sh — the patch must catch the registry lookup failure and
# re-raise SerializerNotInstalled instead of letting a bare KeyError escape.
# Scoped to kombu's serialization module.
set -euo pipefail

WORKSPACE="${WORKSPACE:-/workspace}"
REPO="$WORKSPACE/kombu"

if [[ ! -d "$REPO/.git" ]]; then
    printf '{"score": 0.0, "passed": false, "detail": "kombu workspace repo not found"}\n'
    exit 0
fi

DIFF="$(git -C "$REPO" diff HEAD -- 'kombu/serialization.py' 2>/dev/null; git -C "$REPO" diff --cached -- 'kombu/serialization.py' 2>/dev/null)"
ADDED="$(printf '%s' "$DIFF" | grep '^+' | grep -v '^+++' || true)"

if [[ -z "$(printf '%s' "$ADDED" | tr -d '[:space:]')" ]]; then
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
