#!/usr/bin/env bash
# check_config_valid.sh — verify the agent's answer JSON is structurally valid
# against the schema declared in the task prompt: a non-empty drift_points
# array where each entry has the required keys.
# Uses bash + jq + grep (no python3 in container). Scoring identical to the previous
# python: parse error -> 0.0; drift_points missing/empty/non-list -> 0.0; otherwise
# score = (entries with all REQUIRED_KEYS)/len, round(2dp), passed = unrounded >= 0.5.
set -euo pipefail

export ANSWER_FILE="$WORKSPACE/agent_output/answer.json"

if [[ ! -f "$ANSWER_FILE" ]]; then
    echo '{"score": 0.0, "passed": false, "detail": "No answer.json found"}'
    exit 0
fi

# REQUIRED_KEYS = {config_key, tokio_default, tokio_source_file,
#                  hyper_assumption, hyper_source_file, impact}
REQUIRED_KEYS=(config_key tokio_default tokio_source_file hyper_assumption hyper_source_file impact)

# Parse error -> mirror python (OSError/UnicodeDecodeError/JSONDecodeError).
if ! jq -e . "$ANSWER_FILE" >/dev/null 2>&1; then
    echo '{"score": 0.0, "passed": false, "detail": "Parse error"}'
    exit 0
fi

# drift_points = answer.get('drift_points') if isinstance(answer, dict) else None
# not a list or empty -> 0.0
total=$(jq -r 'if type=="object" and (.drift_points|type=="array") then (.drift_points|length) else 0 end' "$ANSWER_FILE")
if [[ "$total" -eq 0 ]]; then
    echo '{"score": 0.0, "passed": false, "detail": "drift_points missing or empty"}'
    exit 0
fi

# valid = entries that are objects whose keys are a superset of REQUIRED_KEYS
req_json=$(printf '%s\n' "${REQUIRED_KEYS[@]}" | jq -R . | jq -s .)
valid=$(jq --argjson req "$req_json" -r \
  '[.drift_points[] | select(type=="object" and ((keys_unsorted) as $k | $req | all(. as $r | $k | index($r) != null)))] | length' \
  "$ANSWER_FILE")

# score = valid/total; passed uses the UNROUNDED ratio (python semantics).
passed=$(jq -n --argjson v "$valid" --argjson t "$total" 'if ($v/$t) >= 0.5 then true else false end')

# round(valid/total, 2) with python round-half-to-even, formatted like json.dumps.
cents=$(awk -v v="$valid" -v t="$total" 'BEGIN{
  s=(v/t)*100; f=int(s); r=s-f;
  if (r>0.5) f=f+1; else if (r==0.5 && (f%2==1)) f=f+1;
  printf "%d\n", f;
}')
if [[ "$cents" -eq 0 ]]; then
  score="0.0"
elif [[ "$cents" -eq 100 ]]; then
  score="1.0"
else
  score=$(jq -n --argjson c "$cents" '$c/100')
fi

printf '{"score": %s, "passed": %s, "detail": "%s/%s drift_points entries have required schema"}\n' \
  "$score" "$passed" "$valid" "$total"
