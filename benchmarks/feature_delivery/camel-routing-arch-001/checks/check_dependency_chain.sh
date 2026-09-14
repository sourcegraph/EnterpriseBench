#!/usr/bin/env bash
# check_dependency_chain.sh — verify agent traced the reification chain
set -euo pipefail

# Look for agent output
ANSWER="${WORKSPACE:-/workspace}/agent_output/answer.json"
if [[ ! -f "$ANSWER" ]]; then
    printf '{"score": 0.0, "passed": false, "detail": "No agent output found"}\n'
    exit 0
fi

TEXT=$(tr '[:upper:]' '[:lower:]' < "$ANSWER")

FOUND=0
TOTAL=4

# Repo-specific symbols only — the task prompt never names these classes, so
# credit requires the agent to have actually located them in the repo.
echo "$TEXT" | grep -q "routereifier\|route.*reifier" && FOUND=$((FOUND + 1))
echo "$TEXT" | grep -q "routedefinition\|route.*definition" && FOUND=$((FOUND + 1))
echo "$TEXT" | grep -qE "pipeline\.java|processor/pipeline" && FOUND=$((FOUND + 1))
echo "$TEXT" | grep -qE "defaultchannel|channel\.java" && FOUND=$((FOUND + 1))

SCORE=$(awk "BEGIN {printf \"%.2f\", $FOUND/$TOTAL}")
PASSED=$([ "$FOUND" -ge 2 ] && echo true || echo false)
printf '{"score": %s, "passed": %s, "detail": "Found %d/%d reification chain concepts"}\n' "$SCORE" "$PASSED" "$FOUND" "$TOTAL"
