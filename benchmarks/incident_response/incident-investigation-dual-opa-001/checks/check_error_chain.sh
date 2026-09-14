#!/usr/bin/env bash
# Checkpoint 2: Verify agent traced the error across both repos
set -euo pipefail

REPORT="${WORKSPACE:-/workspace}/INCIDENT_REPORT.md"
if [[ ! -f "$REPORT" ]]; then
  printf '{"score": 0.0, "passed": false, "reason": "INCIDENT_REPORT.md not found"}\n'
  exit 0
fi

FOUND=0
TOTAL=4

# Repo-specific artifacts only — generic incident terms (Gatekeeper, parse
# error, import rego.v1) are given in the prompt and must not earn credit
# alone; each hop below requires the actual symbol/path from the trace.

# Mentions controller submission / AddTemplate
if grep -qiE 'AddTemplate|constrainttemplate_controller|cfClient|ConstraintTemplateReconciler' "$REPORT"; then
  FOUND=$((FOUND + 1))
fi

# Mentions framework rego driver/compiler as pin site
if grep -qiE 'driver\.go|compilers\.go|rego/driver' "$REPORT"; then
  FOUND=$((FOUND + 1))
fi

# Mentions OPA parse/compile rejection at the specific file
if grep -qiE 'ast/parser\.go|topdown/eval\.go|parser\.go.*(reject|RegoVersion)' "$REPORT"; then
  FOUND=$((FOUND + 1))
fi

# Mentions the specific version-pin cause, not just the v1-import symptom
if grep -qiE 'SetRegoVersion|RegoV0|RegoV1|RegoVersion' "$REPORT"; then
  FOUND=$((FOUND + 1))
fi

SCORE=$(awk "BEGIN {printf \"%.2f\", $FOUND/$TOTAL}")
if [ "$FOUND" -ge 3 ]; then
  PASSED=true
else
  PASSED=false
fi

printf '{"score": %s, "passed": %s, "reason": "Traced %d/%d error chain steps"}\n' "$SCORE" "$PASSED" "$FOUND" "$TOTAL"
