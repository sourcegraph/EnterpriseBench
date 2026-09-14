#!/usr/bin/env bash
# check_error_chain.sh -- semantic chain matching: agent traces full propagation chain
# Reimplemented in bash+jq+grep (no python3 in container); scoring semantics identical to the prior python3 implementation.
set -euo pipefail

export ANSWER_FILE="$WORKSPACE/agent_output/answer.json"
export GT_FILE="$TASK_DIR/ground_truth.json"

if [[ ! -f "$ANSWER_FILE" ]]; then
    echo '{"score": 0.0, "passed": false, "detail": "No answer.json found"}'
    exit 1
fi

ANSWER_TEXT=$(jq -c . "$ANSWER_FILE" | tr '[:upper:]' '[:lower:]')

# Build keyword groups: for each GT error_chain step, first 3 words len>5 (lowercased).
# A step is "found" when ALL its keywords appear as literal substrings of ANSWER_TEXT.
# (python: keywords=[w for w in step.lower().split() if len(w)>5][:3]; all(kw in text);
#  empty keyword list -> all([]) == True -> counts as found.)
STEPS=$(jq -r '(if type=="object" then (.error_chain // []) else [] end) | length' "$GT_FILE")
found=0
i=0
while [[ $i -lt $STEPS ]]; do
  # emit each keyword on its own line; if no keywords, emits nothing
  kws=$(jq -r --argjson i "$i" '
    (if type=="object" then (.error_chain // []) else [] end)[$i]
    | (if type=="string" then . else "" end)
    | ascii_downcase | splits("[ \t\n\r\f]+") | select(length>5)' "$GT_FILE" | head -n 3)
  step_ok=1
  if [[ -n "$kws" ]]; then
    while IFS= read -r kw; do
      [[ -z "$kw" ]] && continue
      if ! printf '%s' "$ANSWER_TEXT" | grep -qF -- "$kw"; then step_ok=0; break; fi
    done <<<"$kws"
  fi
  [[ $step_ok -eq 1 ]] && found=$((found+1))
  i=$((i+1))
done

# score = found/len(gt_chain) if gt_chain else 0.0 ; output round(score,2) ; pass at unrounded >=0.5
if [[ $STEPS -eq 0 ]]; then
  score="0.0"; passed=false
else
  num=$found; den=$STEPS
  if [[ $num -ge $den ]]; then h=100; else
    q=$((num*100/den)); r=$((num*100%den)); twice=$((2*r))
    if   [[ $twice -lt $den ]]; then h=$q
    elif [[ $twice -gt $den ]]; then h=$((q+1))
    else if [[ $((q%2)) -eq 0 ]]; then h=$q; else h=$((q+1)); fi
    fi
  fi
  intp=$((h/100)); frac=$((h%100))
  if   [[ $frac -eq 0 ]]; then score=$(printf '%d.0' "$intp")
  elif [[ $((frac%10)) -eq 0 ]]; then score=$(printf '%d.%d' "$intp" "$((frac/10))")
  else score=$(printf '%d.%02d' "$intp" "$frac")
  fi
  # passed: unrounded found/STEPS >= 0.5  ->  2*found >= STEPS
  if [[ $((2*found)) -ge $STEPS ]]; then passed=true; else passed=false; fi
fi

printf '{"score": %s, "passed": %s, "detail": "Matched %s/%s chain steps"}\n' "$score" "$passed" "$found" "$STEPS"
