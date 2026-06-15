#!/usr/bin/env bash
# Check that agent proposes or implements a remediation.
set -euo pipefail
WORKSPACE="${WORKSPACE:-${1:-.}}"
ACTIONS="$WORKSPACE/actions.jsonl"

if [ ! -f "$ACTIONS" ]; then
    echo '{"score": 0.0, "message": "actions.jsonl not found"}'
    exit 1
fi

HAS_REMEDIATE=0
grep -qi '"remediate"' "$ACTIONS" && HAS_REMEDIATE=1 || true

if [ "$HAS_REMEDIATE" -eq 1 ]; then
    echo '{"score": 1.0, "message": "Remediation action found"}'
    exit 0
else
    echo '{"score": 0.0, "message": "No remediation action found"}'
    exit 1
fi
