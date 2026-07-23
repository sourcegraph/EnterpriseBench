#!/usr/bin/env bash
# check_boundary_violations.sh — checkpoint "identify_boundary_violations"
#
# The old check grepped for 'applyDecs2305', 'transformer-2023-05' and
# 'typescript/index' and scored found/3. The scored set is right — those are the
# three files where these two features cross package boundaries — but the old
# instruction.md handed one of them over: "The core change is in
# @babel/helper-create-class-features-plugin, with a new helper (applyDecs2305)".
# A `cp instruction.md IMPACT_REPORT.md` copy scored 0.33 here for free.
#
# The helper name has been removed from instruction.md, so all three targets now
# have ZERO hits in it and each one costs a read of the checkout. That is why
# this checkpoint carries no separate evidence gate in ground_truth.json: its
# scored tokens ARE the evidence, and gating a checkpoint on the token it already
# scores would be circular (EnterpriseBench-jn73.2.7.3.1.2).
#
# Fixed strings, never a regex: an unanchored '.' matches hyphens and slashes and
# hides real leaks.
set -euo pipefail

WORKSPACE="${WORKSPACE:-/workspace}"
REPORT="$WORKSPACE/agent_output/IMPACT_REPORT.md"
MAX_REPORT_BYTES=1048576

verdict() { printf '{"score": %s, "passed": %s, "detail": "%s"}\n' "$1" "$2" "$3"; exit 0; }

if [[ -L "$REPORT" ]]; then
  verdict 0.0 false "IMPACT_REPORT.md is a symlink, not a regular file"
fi
if [[ ! -f "$REPORT" ]]; then
  verdict 0.0 false "IMPACT_REPORT.md not found"
fi
if [[ "$(wc -c <"$REPORT")" -gt "$MAX_REPORT_BYTES" ]]; then
  verdict 0.0 false "IMPACT_REPORT.md exceeds ${MAX_REPORT_BYTES} bytes"
fi

FOUND=0
TOTAL=3
NAMED=""
# applyDecs2305            — the decorator runtime helper (@babel/helpers)
# transformer-2023-05      — the decorator transformer (@babel/plugin-proposal-decorators)
# typescript/index         — the TS tuple grammar (@babel/parser)
for tok in "applyDecs2305" "transformer-2023-05" "typescript/index"; do
  if grep -qiF -- "$tok" "$REPORT"; then
    FOUND=$((FOUND + 1))
    NAMED="${NAMED:+$NAMED,}$tok"
  fi
done

SCORE=$(awk "BEGIN {printf \"%.2f\", $FOUND/$TOTAL}")
if [ "$FOUND" -ge 2 ]; then PASSED=true; else PASSED=false; fi

verdict "$SCORE" "$PASSED" "Identified $FOUND/$TOTAL boundary-crossing files (${NAMED:-none})"
