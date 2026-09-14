#!/usr/bin/env bash
# Checkpoint 2: Verify impact classification
set -euo pipefail

REPORT="${WORKSPACE:-/workspace}/rust/IMPACT_REPORT.md"
if [[ ! -f "$REPORT" ]]; then
  printf '{"score": 0.0, "passed": false, "reason": "IMPACT_REPORT.md not found"}\n'
  exit 0
fi

CONTENT=$(cat "$REPORT" | tr '[:upper:]' '[:lower:]')

HAS_MINOR=0
if echo "$CONTENT" | grep -q 'minor'; then HAS_MINOR=1; fi

# The classification must be tied to repo-specific evidence (one of the actual
# changed files/paths), not just the bare word "minor" copied from the prompt.
HAS_EVIDENCE=0
if echo "$CONTENT" | grep -qE 'rmeta/encoder|eval_context\.rs|eval_queries\.rs|gvn\.rs|shims/intrinsics|rustc_metadata/src|rustc_const_eval/src'; then
  HAS_EVIDENCE=1
fi

if [ "$HAS_MINOR" -eq 1 ] && [ "$HAS_EVIDENCE" -eq 1 ]; then
  printf '{"score": 1.0, "passed": true, "reason": "Minor classification found, grounded in a specific changed file"}\n'
elif [ "$HAS_MINOR" -eq 1 ]; then
  printf '{"score": 0.4, "passed": false, "reason": "Minor classification found but not tied to any specific changed file"}\n'
else
  printf '{"score": 0.0, "passed": false, "reason": "Expected minor classification not found"}\n'
fi
