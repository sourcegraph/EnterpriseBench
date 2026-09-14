#!/usr/bin/env bash
# check_refactor_ordering.sh — structural validation: agent's answer must
# contain a non-empty refactor_steps array whose entries have all the keys
# declared in the prompt, including the dependencies array that encodes the
# refactor ordering constraint.
# bash+jq+grep (no python3 in container). Scoring semantics identical to the
# previous python implementation.
set -euo pipefail

export ANSWER_FILE="$WORKSPACE/agent_output/answer.json"

if [[ ! -f "$ANSWER_FILE" ]]; then
    echo '{"score": 0.0, "passed": false, "detail": "No answer.json found"}'
    exit 0
fi

# Parse-error branch (python try/except over the json load).
if ! jq -e . "$ANSWER_FILE" >/dev/null 2>&1; then
    echo '{"score": 0.0, "passed": false, "detail": "Parse error"}'
    exit 0
fi

# steps = answer.refactor_steps if answer is dict else None; require non-empty list.
steps_type=$(jq -r 'if type=="object" then (.refactor_steps | type) else "none" end' "$ANSWER_FILE")
n_steps=$(jq -r 'if (type=="object") and ((.refactor_steps|type)=="array") then (.refactor_steps|length) else 0 end' "$ANSWER_FILE")
if [[ "$steps_type" != "array" || "$n_steps" -eq 0 ]]; then
    echo '{"score": 0.0, "passed": false, "detail": "refactor_steps missing or empty"}'
    exit 0
fi

# valid = entries that are objects containing all REQUIRED_KEYS.
# ordered = of the valid entries, those whose .dependencies is a list.
valid=$(jq -r '
  ["target","current_implementation","target_implementation","source_file","dependencies","breaking_changes"] as $req |
  [ .refactor_steps[]
    | select(type=="object")
    | select( ([keys[]] as $k | all($req[]; . as $r | ($k | index($r)) != null)) ) ]
  | length' "$ANSWER_FILE")
ordered=$(jq -r '
  ["target","current_implementation","target_implementation","source_file","dependencies","breaking_changes"] as $req |
  [ .refactor_steps[]
    | select(type=="object")
    | select( ([keys[]] as $k | all($req[]; . as $r | ($k | index($r)) != null)) )
    | select((.dependencies | type) == "array") ]
  | length' "$ANSWER_FILE")

# score = ((valid/n)+(ordered/n))/2; round(.,2) banker's rounding; passed = score>=0.5
score=$(jq -n --argjson v "$valid" --argjson o "$ordered" --argjson n "$n_steps" '
  (( ($v / $n) + ($o / $n) ) / 2) as $s |
  ($s * 100) as $x | ($x | floor) as $fl | ($x - $fl) as $fr |
  ((if $fr < 0.5 then $fl elif $fr > 0.5 then $fl + 1
    else (if ($fl % 2) == 0 then $fl else $fl + 1 end) end) / 100) | tostring' | tr -d '"')
case "$score" in *.*) ;; *) score="${score}.0";; esac
passed=$(jq -n --argjson sc "$score" 'if $sc >= 0.5 then true else false end')
printf '{"score": %s, "passed": %s, "detail": "%s/%s steps have schema; %s/%s encode ordering"}\n' \
    "$score" "$passed" "$valid" "$n_steps" "$ordered" "$n_steps"
