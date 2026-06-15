#!/usr/bin/env bash
# Check that agent identifies markupsafe as root cause.
set -euo pipefail
WORKSPACE="${WORKSPACE:-${1:-.}}"
ACTIONS="$WORKSPACE/actions.jsonl"

if [ ! -f "$ACTIONS" ]; then
    echo '{"score": 0.0, "message": "actions.jsonl not found"}'
    exit 1
fi

# Look for investigate actions targeting utils.py or markupsafe
HAS_INVESTIGATE=0
grep -qi '"investigate"' "$ACTIONS" && grep -qi 'markupsafe\|utils\|soft_str' "$ACTIONS" && HAS_INVESTIGATE=1 || true

if [ "$HAS_INVESTIGATE" -eq 1 ]; then
    echo '{"score": 1.0, "message": "Root cause investigation found"}'
    exit 0
else
    echo '{"score": 0.0, "message": "No investigation of markupsafe/utils root cause"}'
    exit 1
fi
