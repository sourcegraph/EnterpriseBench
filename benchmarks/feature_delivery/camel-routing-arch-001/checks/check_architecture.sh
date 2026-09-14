#!/usr/bin/env bash
# check_architecture.sh — verify agent produced coherent architectural analysis
set -euo pipefail

ANSWER="${WORKSPACE:-/workspace}/agent_output/answer.json"
if [[ ! -f "$ANSWER" ]]; then
    printf '{"score": 0.0, "passed": false, "detail": "No agent output found"}\n'
    exit 0
fi

TEXT=$(tr '[:upper:]' '[:lower:]' < "$ANSWER")

FOUND=0
TOTAL=3

# Requires the concrete default-implementation classes under camel-support, not
# just repeating the abstract Component/Endpoint/Consumer/Processor/Producer
# hierarchy already named in the task prompt.
echo "$TEXT" | grep -q "defaultcomponent\|defaultendpoint\|defaultconsumer\|defaultproducer" && FOUND=$((FOUND + 1))
# Requires a specific named architecture pattern from the real analysis, not
# the generic "pattern"/"EIP" words already used to describe the task itself.
echo "$TEXT" | grep -q "pipes-and-filters\|pipes and filters\|ports-and-adapters\|ports and adapters\|hexagonal" && FOUND=$((FOUND + 1))
# Must have reasonable length (at least 500 chars of analysis)
LEN=$(echo "$TEXT" | wc -c)
[ "$LEN" -ge 500 ] && FOUND=$((FOUND + 1))

SCORE=$(awk "BEGIN {printf \"%.2f\", $FOUND/$TOTAL}")
PASSED=$([ "$FOUND" -ge 2 ] && echo true || echo false)
printf '{"score": %s, "passed": %s, "detail": "Architecture analysis quality: %d/%d criteria met"}\n' "$SCORE" "$PASSED" "$FOUND" "$TOTAL"
