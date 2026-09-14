#!/usr/bin/env bash
# Checkpoint 2: Verify agent traced the reconciliation failure chain
set -euo pipefail

export REPORT="${WORKSPACE:-/workspace}/flux2/INCIDENT_REPORT.md"
if [[ ! -f "$REPORT" ]]; then
  printf '{"score": 0.0, "passed": false, "reason": "INCIDENT_REPORT.md not found"}\n'
  exit 0
fi

FOUND=0
TOTAL=4

# Must mention the helm-controller v0.36 -> v0.37 upgrade as the trigger for
# in-progress HelmReleases retaining old-format conditions.
if grep -qiE 'v0\.36.*v0\.37|0\.36.*0\.37|helm-controller.*upgrad.*(0\.36|0\.37)' "$REPORT"; then
  FOUND=$((FOUND + 1))
fi

# Must name the helm-controller release writer that emits new-format conditions
if grep -qiE 'internal/reconcile/release\.go' "$REPORT"; then
  FOUND=$((FOUND + 1))
fi

# Must name the helm-controller reconciler that misparses old-format conditions
if grep -qiE 'internal/controller/helmrelease_controller\.go|helmrelease_controller\.go' "$REPORT"; then
  FOUND=$((FOUND + 1))
fi

# Must describe the infinite reconcile loop and downstream impact on
# kustomize-controller / Flux CLI consumers (the discriminating hop — not
# just re-stating the "not ready" symptom already given in the prompt).
if grep -qiE 'infinite.*loop|reconcil.*loop|re.?reconcil.*loop|endless.*reconcil' "$REPORT"; then
  FOUND=$((FOUND + 1))
fi

if [ "$TOTAL" -gt 0 ]; then
  SCORE=$(awk "BEGIN {printf \"%.2f\", $FOUND/$TOTAL}")
else
  SCORE="0.00"
fi
if [ "$FOUND" -ge 2 ]; then
  PASSED=true
else
  PASSED=false
fi

printf '{"score": %s, "passed": %s, "reason": "Traced %d/%d reconciliation failure steps"}\n' "$SCORE" "$PASSED" "$FOUND" "$TOTAL"
