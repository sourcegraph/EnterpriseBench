#!/usr/bin/env bash
# Check that agent triages the issue.
set -euo pipefail
WORKSPACE="${WORKSPACE:-${1:-.}}"
ACTIONS="$WORKSPACE/actions.jsonl"

if [ ! -f "$ACTIONS" ]; then
    echo '{"score": 0.0, "message": "actions.jsonl not found"}'
    exit 1
fi

HAS_TRIAGE=0
grep -qi '"triage"' "$ACTIONS" && HAS_TRIAGE=1 || true

if [ "$HAS_TRIAGE" -eq 1 ]; then
    echo '{"score": 1.0, "message": "Triage action found"}'
    exit 0
else
    echo '{"score": 0.0, "message": "No triage action found"}'
    exit 1
fi
