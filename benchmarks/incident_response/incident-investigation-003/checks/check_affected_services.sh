#!/usr/bin/env bash
# Checkpoint 3: Verify agent listed affected datasources
set -euo pipefail

export REPORT="${WORKSPACE:-/workspace}/grafana/INCIDENT_REPORT.md"
if [[ ! -f "$REPORT" ]]; then
  printf '{"score": 0.0, "passed": false, "reason": "INCIDENT_REPORT.md not found"}\n'
  exit 0
fi

FOUND=0
TOTAL=3

# Must cite the converter implementation file where the shared bug lives --
# "Prometheus/Loki datasource" alone is given in the prompt and proves nothing.
if grep -qiE 'pkg/util/converter/prom\.go|util/converter/prom\.go' "$REPORT"; then
  FOUND=$((FOUND + 1))
fi

# Must cite the reference/test file, or explicitly name the shared code path,
# showing the agent looked at how the converter is exercised/reused.
if grep -qiE 'prom_test\.go|shares?.*the.*converter|shared.*converter.*(path|code)' "$REPORT"; then
  FOUND=$((FOUND + 1))
fi

# Must mention Loki as also affected (given in prompt, but only credited
# alongside the file-level evidence above)
if grep -qiE 'loki' "$REPORT"; then
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

printf '{"score": %s, "passed": %s, "reason": "Identified %d/%d affected datasources"}\n' "$SCORE" "$PASSED" "$FOUND" "$TOTAL"
