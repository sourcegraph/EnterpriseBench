#!/usr/bin/env bash
set -euo pipefail

REPORT="${WORKSPACE:-/workspace}/analysis/IMPACT_REPORT.md"
if [[ ! -f "$REPORT" ]]; then
  printf '{"score": 0.0, "passed": false, "reason": "IMPACT_REPORT.md not found"}\n'
  exit 0
fi

CONTENT=$(cat "$REPORT" | tr '[:upper:]' '[:lower:]')
FOUND=0
TOTAL=2
# Must identify runtime fatal behavior AND cite the producer-side file that
# enforces it — generic "fatal"/"reject" language alone (already implied by
# the prompt's scenario) is not sufficient evidence of repo investigation.
if echo "$CONTENT" | grep -qE 'runtime.*fatal|fatal.*runtime|reject.*request|connection.*refuse' \
  && echo "$CONTENT" | grep -qE 'api_type_oracle\.cc|ads_integration_test\.cc|config_test\.cc'; then FOUND=$((FOUND + 1)); fi
# Must distinguish the phased enforcement (warning -> fatal -> removed) AND
# tie it to a specific consumer file that breaks under the classification.
if echo "$CONTENT" | grep -qE 'warn.*fatal|phase|gradual|override|rollout' \
  && echo "$CONTENT" | grep -qE 'cluster\.go|cluster_builder\.go|listener\.go|route\.go|ads\.go|cds\.go|eds\.go|cache\.go|resource\.go|simple_test\.go|server\.go|util\.go'; then FOUND=$((FOUND + 1)); fi

SCORE=$(awk "BEGIN {printf \"%.2f\", $FOUND/$TOTAL}")
if [ "$FOUND" -ge 2 ]; then PASSED=true; else PASSED=false; fi

printf '{"score": %s, "passed": %s, "reason": "Classified %d/%d breakage aspects"}\n' "$SCORE" "$PASSED" "$FOUND" "$TOTAL"
