#!/usr/bin/env bash
# check_race_condition.sh — verify the agent's answer mentions the key concepts
# describing the Flyway/Hibernate ordering race (semantic keyword match against
# ground_truth.race_condition_terms).
# Implemented in bash+jq+grep (no python3 in container). Scoring identical to the
# previous python3 implementation: count GT race_condition_terms (lowercased) found
# as substrings in json.dumps(answer).lower(); score=found/len (round 2dp), >=0.5.
set -euo pipefail

export ANSWER_FILE="$WORKSPACE/agent_output/answer.json"
export GT_FILE="$TASK_DIR/ground_truth.json"

if [[ ! -f "$ANSWER_FILE" ]]; then
    echo '{"score": 0.0, "passed": false, "detail": "No answer.json found"}'
    exit 0
fi
if [[ ! -f "$GT_FILE" ]]; then
    echo '{"score": 0.0, "passed": false, "detail": "No ground_truth.json found"}' >&2
    exit 1
fi

answer_text=$(jq -r '
  def pyd:
    if type=="object" then "{" + ([to_entries[] | (.key|tojson) + ": " + (.value|pyd)] | join(", ")) + "}"
    elif type=="array" then "[" + ([.[]|pyd] | join(", ")) + "]"
    else tojson end;
  pyd' "$ANSWER_FILE" | tr '[:upper:]' '[:lower:]')

n_terms=$(jq -r 'if (.race_condition_terms | type) == "array" then (.race_condition_terms | length) else 0 end' "$GT_FILE")
if [[ "$n_terms" -eq 0 ]]; then
    echo '{"score": 0.0, "passed": false, "detail": "No race_condition_terms in ground_truth"}'
    exit 0
fi

mapfile -t terms < <(jq -r '.race_condition_terms[] | select(type=="string")' "$GT_FILE")

found=0
for t in "${terms[@]}"; do
  tl=$(printf '%s' "$t" | tr '[:upper:]' '[:lower:]')
  if printf '%s' "$answer_text" | grep -qF -- "$tl"; then found=$((found + 1)); fi
done

round2() {
  awk -v x="$1" 'BEGIN{ s=sprintf("%.2f", x); sub(/0+$/, "", s); if (s ~ /\.$/) s=s"0"; print s }'
}
raw=$(jq -n --argjson f "$found" --argjson n "$n_terms" '$f/$n')
score=$(round2 "$raw")
passed=$(jq -rn --argjson f "$found" --argjson n "$n_terms" 'if ($f/$n)>=0.5 then "true" else "false" end')

printf '{"score": %s, "passed": %s, "detail": "Mentioned %s/%s race-condition concept terms"}\n' \
  "$score" "$passed" "$found" "$n_terms"
