#!/usr/bin/env bash
# check_dependency_chain.sh — structural validation of the agent's
# dependency_chain answer: each entry must have the required keys declared
# in the task prompt.
# Implemented in bash+jq+grep (no python3 in container). Scoring identical to the
# previous python3 implementation: an entry is valid iff it is an object whose keys
# are a superset of the required set; score = valid/len(chain) (round 2dp), >=0.5.
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

# chain must be a non-empty array under a top-level object key.
chain_ok=$(jq -r 'if (type=="object") and (.dependency_chain|type=="array") and ((.dependency_chain|length)>0)
                  then "yes" else "no" end' "$ANSWER_FILE")
if [[ "$chain_ok" != "yes" ]]; then
    echo '{"score": 0.0, "passed": false, "detail": "dependency_chain missing or empty"}'
    exit 0
fi

total=$(jq -r '.dependency_chain | length' "$ANSWER_FILE")
# valid = entries that are objects whose key set is a superset of REQUIRED_KEYS.
valid=$(jq -r '
  ["from","to","relationship_type","nextjs_file","webpack_file","fragility"] as $req
  | [ .dependency_chain[]
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

printf '{"score": %s, "passed": %s, "detail": "%s/%s dependency_chain entries have required schema"}\n' \
  "$score" "$passed" "$valid" "$total"
