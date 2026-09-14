#!/usr/bin/env bash
# Checkpoint 3: Verify agent identified all three affected components
set -euo pipefail

REPORT="${WORKSPACE:-/workspace}/INCIDENT_REPORT.md"
if [[ ! -f "$REPORT" ]]; then
  printf '{"score": 0.0, "passed": false, "reason": "INCIDENT_REPORT.md not found"}\n'
  exit 0
fi

FOUND=0
TOTAL=3

# Repo-specific artifacts only — generic terms already given in the prompt
# (Gatekeeper, ConstraintTemplate, ast.Compiler, topdown evaluation) must not
# earn credit on their own.

# Framework rego driver — specific path
if grep -qiE 'drivers/rego/driver\.go|rego/driver\.go|driver\.go.*eval' "$REPORT"; then
  FOUND=$((FOUND + 1))
fi

# OPA's ast or topdown — specific file
if grep -qiE 'ast/parser\.go|topdown/eval\.go|parser\.go.*RegoVersion|eval\.go.*RegoVersion' "$REPORT"; then
  FOUND=$((FOUND + 1))
fi

# Gatekeeper controller — specific symbol, not just the product name
if grep -qiE 'constrainttemplate_controller|ConstraintTemplateReconciler|controller.*Reconcile|Reconcile.*controller' "$REPORT"; then
  FOUND=$((FOUND + 1))
fi

SCORE=$(awk "BEGIN {printf \"%.2f\", $FOUND/$TOTAL}")
if [ "$FOUND" -ge 2 ]; then
  PASSED=true
else
  PASSED=false
fi

printf '{"score": %s, "passed": %s, "reason": "Identified %d/%d affected components"}\n' "$SCORE" "$PASSED" "$FOUND" "$TOTAL"
