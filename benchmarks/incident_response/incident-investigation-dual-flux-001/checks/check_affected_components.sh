#!/usr/bin/env bash
# Checkpoint 3: Verify agent identified affected Flux resources
set -euo pipefail

export REPORT="${WORKSPACE:-/workspace}/flux2/INCIDENT_REPORT.md"
if [[ ! -f "$REPORT" ]]; then
  printf '{"score": 0.0, "passed": false, "reason": "INCIDENT_REPORT.md not found"}\n'
  exit 0
fi

FOUND=0
TOTAL=3

# Repo-specific artifacts only — generic terms (HelmRelease, kustomize-
# controller) are already given in the prompt and must not earn credit alone.

# Must name the helm-controller reconciler that misparses old-format conditions
if grep -qiE 'internal/controller/helmrelease_controller\.go|helmrelease_controller\.go' "$REPORT"; then
  FOUND=$((FOUND + 1))
fi

# Must name the helm-controller release writer that emits new-format conditions
if grep -qiE 'internal/reconcile/release\.go' "$REPORT"; then
  FOUND=$((FOUND + 1))
fi

# Must reference the HelmRelease v2beta2 API/type surface that changed
if grep -qiE 'api/v2beta2/helmrelease_types\.go|api/v2beta2/condition_types\.go' "$REPORT"; then
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

printf '{"score": %s, "passed": %s, "reason": "Identified %d/%d affected Flux resources"}\n' "$SCORE" "$PASSED" "$FOUND" "$TOTAL"
