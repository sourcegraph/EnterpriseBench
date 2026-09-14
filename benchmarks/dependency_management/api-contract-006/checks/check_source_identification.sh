#!/usr/bin/env bash
set -euo pipefail

REPORT="${WORKSPACE:-/workspace}/analysis/IMPACT_REPORT.md"
if [[ ! -f "$REPORT" ]]; then
  printf '{"score": 0.0, "passed": false, "reason": "IMPACT_REPORT.md not found"}\n'
  exit 0
fi

FOUND=0
TOTAL=3
# Naming the xDS v2 topic alone is just the prompt's subject matter — also
# require the specific breaking-change PR(s) from the producer repo.
if grep -qiE 'xds.*v2|v2.*xds|v2.*deprecat' "$REPORT" && grep -qE '14223|15481' "$REPORT"; then FOUND=$((FOUND + 1)); fi
# "enforcement" is asked about in the prompt itself — require the actual
# producer-side file where that enforcement lives.
if grep -qiE 'fatal|reject|enforcement' "$REPORT" && grep -qiE 'api_type_oracle\.cc|ads_integration_test\.cc|config_test\.cc' "$REPORT"; then FOUND=$((FOUND + 1)); fi
# "migrate to v3" is stated as context in the prompt — require citation of a
# real consumer-side fix PR that encodes the migration.
if grep -qiE 'v3.*migrat|migrat.*v3|xds.*v3' "$REPORT" && grep -qE '415|22919|23962|24588' "$REPORT"; then FOUND=$((FOUND + 1)); fi

SCORE=$(awk "BEGIN {printf \"%.2f\", $FOUND/$TOTAL}")
if [ "$FOUND" -ge 3 ]; then PASSED=true; else PASSED=false; fi

printf '{"score": %s, "passed": %s, "reason": "Identified %d/%d source elements"}\n' "$SCORE" "$PASSED" "$FOUND" "$TOTAL"
