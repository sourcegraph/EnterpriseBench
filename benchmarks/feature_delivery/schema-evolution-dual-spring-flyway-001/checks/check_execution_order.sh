#!/usr/bin/env bash
# check_execution_order.sh — structural validation: agent's answer must contain
# a non-empty execution_order array whose entries have the keys declared in
# the prompt (step, file, class).
# Implemented in bash+jq+grep (no python3 in container). Scoring identical to the
# previous python3 implementation: an entry is valid iff it is an object whose keys
# are a superset of {step,file,class}; score = valid/len(order) (round 2dp), >=0.5.
set -euo pipefail

export ANSWER_FILE="$WORKSPACE/agent_output/answer.json"

if [[ ! -f "$ANSWER_FILE" ]]; then
    echo '{"score": 0.0, "passed": false, "detail": "No answer.json found"}'
    exit 0
fi

# Parse error (invalid JSON) -> zero score, exit 0 (matches python try/except).
if ! jq -e . "$ANSWER_FILE" >/dev/null 2>&1; then
    echo '{"score": 0.0, "passed": false, "detail": "Parse error"}'
    exit 0
fi

# order must be a non-empty array under a top-level object key.
order_ok=$(jq -r 'if (type=="object") and (.execution_order|type=="array") and ((.execution_order|length)>0)
                  then "yes" else "no" end' "$ANSWER_FILE")
if [[ "$order_ok" != "yes" ]]; then
    echo '{"score": 0.0, "passed": false, "detail": "execution_order missing or empty"}'
    exit 0
fi

total=$(jq -r '.execution_order | length' "$ANSWER_FILE")
valid=$(jq -r '
  ["step","file","class"] as $req
  | [ .execution_order[]
      | select(type=="object")
      | (keys) as $k
      | select( ($req - $k) | length == 0 ) ]
  | length' "$ANSWER_FILE")

round2() {
  awk -v x="$1" 'BEGIN{ s=sprintf("%.2f", x); sub(/0+$/, "", s); if (s ~ /\.$/) s=s"0"; print s }'
}
raw=$(jq -n --argjson v "$valid" --argjson t "$total" '$v/$t')
score=$(round2 "$raw")
passed=$(jq -rn --argjson v "$valid" --argjson t "$total" 'if ($v/$t)>=0.5 then "true" else "false" end')

printf '{"score": %s, "passed": %s, "detail": "%s/%s execution_order entries have required schema"}\n' \
  "$score" "$passed" "$valid" "$total"
