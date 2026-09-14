#!/usr/bin/env bash
# Checkpoint 1: Verify agent identifies the schema change (migration + model)
set -euo pipefail

REPORT="${WORKSPACE:-/workspace}/zulip/SCHEMA_IMPACT.md"
if [[ ! -f "$REPORT" ]]; then
  printf '{"score": 0.0, "passed": false, "reason": "SCHEMA_IMPACT.md not found"}\n'
  exit 0
fi

# Repo-specific artifacts only — the prompt names the feature (deactivated
# field on NamedUserGroup) but not the migration filename or model file path,
# so both conditions require the agent to have actually located them in the repo.
FOUND=0
TOTAL=2
if grep -qE '0578[_.]*namedusergroup_deactivated|zerver/migrations/0578' "$REPORT"; then FOUND=$((FOUND + 1)); fi
if grep -qE 'zerver/models/groups\.py' "$REPORT"; then FOUND=$((FOUND + 1)); fi

SCORE=$(awk "BEGIN {printf \"%.2f\", $FOUND/$TOTAL}")
if [ "$FOUND" -ge 2 ]; then PASSED=true; else PASSED=false; fi

printf '{"score": %s, "passed": %s, "reason": "Identified %d/%d schema change elements"}\n' "$SCORE" "$PASSED" "$FOUND" "$TOTAL"
