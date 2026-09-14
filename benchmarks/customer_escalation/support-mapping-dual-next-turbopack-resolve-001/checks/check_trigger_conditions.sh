#!/usr/bin/env bash
# check_trigger_conditions.sh -- semantic conditions match
# bash+jq+grep (no python3 in container). Scoring identical to prior python:
# answer_text = lowercased compact JSON of the whole answer; per GT condition,
# keywords = first 3 lowercased words >5 chars; condition counts as found when
# ALL its keywords are literal substrings of answer_text (empty keyword list =>
# found, matching python all([])==True); score = found/len, round 2, passes >=0.5.
set -euo pipefail

export ANSWER_FILE="$WORKSPACE/agent_output/answer.json"
export GT_FILE="$TASK_DIR/ground_truth.json"

if [[ ! -f "$ANSWER_FILE" ]]; then
    echo '{"score": 0.0, "passed": false, "detail": "No answer.json found"}'
    exit 1
fi

# answer_text = json.dumps(answer).lower() (whole-object full-text search)
answer_text=$(jq -c . "$ANSWER_FILE" | tr '[:upper:]' '[:lower:]')

conds_total=$(jq '(.trigger_conditions // []) | length' "$GT_FILE")

found=0
for ((i = 0; i < conds_total; i++)); do
    cond_lc=$(jq -r ".trigger_conditions[$i]" "$GT_FILE" | tr '[:upper:]' '[:lower:]')
    # keywords = first 3 words with length > 5
    keywords=()
    set -f
    for word in $cond_lc; do
        if [[ ${#word} -gt 5 ]]; then
            keywords+=("$word")
            [[ ${#keywords[@]} -ge 3 ]] && break
        fi
    done
    set +f
    all_match=1
    for kw in "${keywords[@]}"; do
        if ! printf '%s' "$answer_text" | grep -qF -- "$kw"; then
            all_match=0
            break
        fi
    done
    [[ $all_match -eq 1 ]] && found=$((found + 1))
done

if [[ "$conds_total" -eq 0 ]]; then
    score_raw=0.0
else
    score_raw=$(jq -n --argjson f "$found" --argjson t "$conds_total" '$f / $t')
fi

read -r score passed < <(jq -n --argjson r "$score_raw" '
  (($r * 100 | round) / 100) as $s
  | (if ($s | floor) == $s then "\($s).0" else "\($s)" end) as $sf
  | "\($sf) \(if $s >= 0.5 then "true" else "false" end)"' | tr -d '"')

printf '{"score": %s, "passed": %s, "detail": "Matched %s/%s trigger conditions"}\n' \
  "$score" "$passed" "$found" "$conds_total"
