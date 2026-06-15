#!/usr/bin/env bash
# Check that agent communicates findings.
set -euo pipefail
WORKSPACE="${WORKSPACE:-${1:-.}}"
ACTIONS="$WORKSPACE/actions.jsonl"

if [ ! -f "$ACTIONS" ]; then
    echo '{"score": 0.0, "message": "actions.jsonl not found"}'
    exit 1
fi

HAS_COMMUNICATE=0
grep -qi '"communicate"' "$ACTIONS" && HAS_COMMUNICATE=1 || true

if [ "$HAS_COMMUNICATE" -eq 1 ]; then
    echo '{"score": 1.0, "message": "Communication action found"}'
    exit 0
else
    echo '{"score": 0.0, "message": "No communication action found"}'
    exit 1
fi
