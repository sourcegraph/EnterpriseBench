#!/usr/bin/env bash
# check_dead_code_locations.sh — structural validation: agent's answer must
# contain a non-empty dead_code_locations array whose entries have all the
# keys declared in the prompt.
#
# Reimplemented in bash+jq (no python3 in container — the task image ships
# bash/grep/jq but not python3, so the previous `python3 -c` body exited 127).
# Scoring is identical: an entry is valid when it is an object containing every
# required key; score = valid / len(dead_code_locations), pass at >= 0.5.
set -euo pipefail

export ANSWER_FILE="$WORKSPACE/agent_output/answer.json"

# Required keys every dead_code_locations entry must declare.
REQUIRED_KEYS=(file class_or_function dead_branch_condition removal_requires_migration explanation)

if [[ ! -f "$ANSWER_FILE" ]]; then
    echo '{"score": 0.0, "passed": false, "detail": "No answer.json found"}'
    exit 0
fi

# Malformed JSON -> generic Parse error, exit 0.
if ! jq -e . "$ANSWER_FILE" >/dev/null 2>&1; then
    echo '{"score": 0.0, "passed": false, "detail": "Parse error"}'
    exit 0
fi

# locs = answer.get('dead_code_locations') only when answer is a dict.
loc_type=$(jq -r 'if type=="object" and has("dead_code_locations")
                  then (.dead_code_locations | type) else "none" end' "$ANSWER_FILE")
total=0
if [[ "$loc_type" == "array" ]]; then
    total=$(jq '.dead_code_locations | length' "$ANSWER_FILE")
fi

if [[ "$loc_type" != "array" || "$total" -eq 0 ]]; then
    echo '{"score": 0.0, "passed": false, "detail": "dead_code_locations missing or empty"}'
    exit 0
fi

# Count entries that are objects containing every required key (presence only).
keys_json=$(printf '%s\n' "${REQUIRED_KEYS[@]}" | jq -R . | jq -s .)
valid=$(jq --argjson req "$keys_json" '
  [ .dead_code_locations[]
    | select(type=="object")
    | (keys) as $k
    | select( ($req - $k) == [] ) ] | length' "$ANSWER_FILE")

score=$(jq -n --argjson f "$valid" --argjson t "$total" '
  (($f / $t * 100) | round) / 100
  | if . == (. | floor) then "\(.).0" else (. | tostring) end' | tr -d '"')
passed=$(jq -n --argjson f "$valid" --argjson t "$total" 'if ($f / $t) >= 0.5 then true else false end')
printf '{"score": %s, "passed": %s, "detail": "%s/%s dead_code_locations have required schema"}\n' \
    "$score" "$passed" "$valid" "$total"
