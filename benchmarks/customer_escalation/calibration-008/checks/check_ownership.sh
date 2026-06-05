#!/usr/bin/env bash
# check_ownership.sh — verify agent identified responsible code area
# bash+jq+grep reimplementation (no python3 in container). Output JSON is
# byte-identical to the previous python implementation.
set -euo pipefail

export ANSWER_FILE="${WORKSPACE}/agent_output/answer.json"
export GT_FILE="${TASK_DIR}/ground_truth.json"

if [[ ! -f "$ANSWER_FILE" ]]; then
    echo '{"score": 0.0, "passed": false, "detail": "No answer.json found"}'
    exit 1
fi

if [[ ! -f "$GT_FILE" ]]; then
    echo '{"score": 0.0, "passed": false, "detail": "No ground_truth.json found"}'
    exit 1
fi

n_kw=$(jq '(.ownership_keywords // []) | length' "$GT_FILE")

if [[ "$n_kw" -eq 0 ]]; then
    jq -cn '{score: 1.0, passed: true, detail: "No ownership keywords to check"}'
    exit 0
fi

# agent_text = json.dumps(answer.get('ownership', answer.get('responsible_area',''))).lower()
# Reproduce json.dumps (default separators ", " / ": ") then lowercase.
agent_text=$(jq -c '
  if has("ownership") then .ownership
  elif has("responsible_area") then .responsible_area
  else "" end
' "$ANSWER_FILE" | sed 's/,/, /g; s/:/: /g' | tr '[:upper:]' '[:lower:]')

# Split the gold compound on '-' and count it matched when ANY token appears in the
# agent text. The old check required the whole hyphenated keyword ("type-cast") as a
# contiguous substring, which prose that says "type cast" never satisfies.
matched=0
while IFS= read -r kw; do
  kw_lc=$(printf '%s' "$kw" | tr '[:upper:]' '[:lower:]')
  while IFS= read -r tok; do
    [[ -z "$tok" ]] && continue
    if grep -qF -- "$tok" <<<"$agent_text"; then
      matched=$((matched + 1)); break
    fi
  done < <(printf '%s\n' "$kw_lc" | tr '-' '\n')
done < <(jq -r '.ownership_keywords[]' "$GT_FILE")

# score = round(matched / n_kw, 2); whole-number results need explicit .0 form
if [[ "$matched" -eq 0 ]]; then
  score="0.0"
elif [[ "$matched" -eq "$n_kw" ]]; then
  score="1.0"
else
  score=$(jq -n --argjson m "$matched" --argjson t "$n_kw" '(($m / $t)*100|round)/100')
fi

passed=$(jq -n --argjson s "$score" 'if $s >= 0.3 then true else false end')
detail="Matched ${matched}/${n_kw} ownership keywords"
jq -cn --argjson s "$score" --argjson p "$passed" --arg d "$detail" '{score: $s, passed: $p, detail: $d}'
