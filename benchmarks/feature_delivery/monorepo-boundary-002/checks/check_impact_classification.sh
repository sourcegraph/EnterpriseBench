#!/usr/bin/env bash
# Checkpoint 2: Verify impact classification
set -euo pipefail

REPORT="${WORKSPACE:-/workspace}/babel/IMPACT_REPORT.md"
if [[ ! -f "$REPORT" ]]; then
  printf '{"score": 0.0, "passed": false, "reason": "IMPACT_REPORT.md not found"}\n'
  exit 0
fi

CONTENT=$(cat "$REPORT" | tr '[:upper:]' '[:lower:]')

# A correct "minor" classification must be tied to the actual repo-specific
# evidence for it: the new applyDecs2311 runtime helper (the concrete artifact
# that makes this an additive/minor change), not just the word "minor" on its
# own (which also appears in the prompt's major/minor/patch/none rubric).
HAS_MINOR=0
HAS_EVIDENCE=0
if echo "$CONTENT" | grep -q 'minor'; then HAS_MINOR=1; fi
if echo "$CONTENT" | grep -qE 'applydecs2311|babel-helpers/src/helpers'; then HAS_EVIDENCE=1; fi

if [ "$HAS_MINOR" -eq 1 ] && [ "$HAS_EVIDENCE" -eq 1 ]; then
  printf '{"score": 1.0, "passed": true, "reason": "Correct classification found with supporting evidence"}\n'
else
  printf '{"score": 0.0, "passed": false, "reason": "Expected minor classification tied to applyDecs2311/babel-helpers evidence not found"}\n'
fi
