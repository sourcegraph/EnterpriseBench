#!/usr/bin/env bash
# check_trigger_conditions.sh — verify the agent identified the conditions that
# trigger the Next.js + Module Federation alias conflict (semantic keyword
# match against ground_truth.trigger_conditions).
# bash+jq+grep (no python3 in container). Scoring identical to prior python:
# answer_text = lowercased compact JSON of the answer; per GT condition (string
# only), keywords = first 3 lowercased words >5 chars; condition is found when it
# has >=1 keyword and ALL keywords are literal substrings of answer_text; score =
# found/len, round 2, passes >=0.5. Parse/read errors => score 0.0 "Parse error".
set -euo pipefail

export ANSWER_FILE="$WORKSPACE/agent_output/answer.json"
export GT_FILE="$TASK_DIR/ground_truth.json"

if [[ ! -f "$ANSWER_FILE" ]]; then
    echo '{"score": 0.0, "passed": false, "detail": "No answer.json found"}'
    exit 1
fi

# try: load+parse both files (OSError/UnicodeDecodeError/JSONDecodeError => Parse error)
if ! answer_text=$(jq -c . "$ANSWER_FILE" 2>/dev/null | tr '[:upper:]' '[:lower:]') \
   || ! jq -e . "$GT_FILE" >/dev/null 2>&1 \
   || [[ -z "$answer_text" ]]; then
    echo '{"score": 0.0, "passed": false, "detail": "Parse error"}'
    exit 0
fi

# conditions = gt.trigger_conditions if gt is dict and value is a non-empty list
conds_total=$(jq 'if type=="object" and (.trigger_conditions|type)=="array"
                  then (.trigger_conditions|length) else 0 end' "$GT_FILE")
if [[ "$conds_total" -eq 0 ]]; then
    echo '{"score": 0.0, "passed": false, "detail": "No trigger_conditions in ground_truth"}'
    exit 0
fi

found=0
for ((i = 0; i < conds_total; i++)); do
    # skip non-string conditions
    ctype=$(jq -r ".trigger_conditions[$i] | type" "$GT_FILE")
    [[ "$ctype" == "string" ]] || continue
    cond_lc=$(jq -r ".trigger_conditions[$i]" "$GT_FILE" | tr '[:upper:]' '[:lower:]')
    keywords=()
    set -f
    for word in $cond_lc; do
        if [[ ${#word} -gt 5 ]]; then
            keywords+=("$word")
            [[ ${#keywords[@]} -ge 3 ]] && break
        fi
    done
    set +f
    # python: if keywords and all(kw in answer_text for kw in keywords)
    [[ ${#keywords[@]} -eq 0 ]] && continue
    all_match=1
    for kw in "${keywords[@]}"; do
        if ! printf '%s' "$answer_text" | grep -qF -- "$kw"; then
            all_match=0
            break
        fi
    done
    [[ $all_match -eq 1 ]] && found=$((found + 1))
done

read -r score passed < <(jq -n --argjson f "$found" --argjson t "$conds_total" '
  (($f / $t * 100 | round) / 100) as $s
  | (if ($s | floor) == $s then "\($s).0" else "\($s)" end) as $sf
  | "\($sf) \(if $s >= 0.5 then "true" else "false" end)"' | tr -d '"')

printf '{"score": %s, "passed": %s, "detail": "Matched %s/%s trigger conditions"}\n' \
  "$score" "$passed" "$found" "$conds_total"
