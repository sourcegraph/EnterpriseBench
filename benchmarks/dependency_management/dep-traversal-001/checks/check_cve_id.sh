#!/usr/bin/env bash
# Checkpoint 1: Verify agent identified the correct CVE and package
set -euo pipefail

REPORT="${WORKSPACE:-/workspace}/BLAST_RADIUS.md"
if [[ ! -f "$REPORT" ]]; then
  printf '{"score": 0.0, "passed": false, "reason": "BLAST_RADIUS.md not found"}\n'
  exit 0
fi

FOUND=0
TOTAL=2
if grep -qi 'CVE-2021-23337' "$REPORT"; then FOUND=$((FOUND + 1)); fi
# The fixed-version threshold (4.17.21) is stated in the prompt itself, so it
# is not evidence the agent looked at the repo. Require the version actually
# pinned in the workspace's checked-out lodash (package.json says 4.17.20) —
# an agent can only produce this by opening /workspace/lodash/package.json.
if grep -qE '4\.17\.20' "$REPORT"; then FOUND=$((FOUND + 1)); fi

SCORE=$(awk "BEGIN {printf \"%.2f\", $FOUND/$TOTAL}")
if [ "$FOUND" -ge 2 ]; then PASSED=true; else PASSED=false; fi

printf '{"score": %s, "passed": %s, "reason": "Identified %d/%d CVE details"}\n' "$SCORE" "$PASSED" "$FOUND" "$TOTAL"
