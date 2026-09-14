#!/usr/bin/env bash
# check_error_chain.sh — verify the agent's answer mentions the steps in the
# Next.js → webpack → Module Federation alias-conflict propagation chain
# (semantic keyword match against ground_truth.error_chain).
# Reimplemented in bash+jq+grep (no python3 in container); scoring semantics identical to the prior python3 implementation.
set -euo pipefail

export ANSWER_FILE="$WORKSPACE/agent_output/answer.json"
export GT_FILE="$TASK_DIR/ground_truth.json"

if [[ ! -f "$ANSWER_FILE" ]]; then
    echo '{"score": 0.0, "passed": false, "detail": "No answer.json found"}'
    exit 1
fi

# Parse guard: invalid JSON / unreadable in either file -> "Parse error" (exit 0).
if ! jq -e . "$ANSWER_FILE" >/dev/null 2>&1 || ! jq -e . "$GT_FILE" >/dev/null 2>&1; then
  echo '{"score": 0.0, "passed": false, "detail": "Parse error"}'
  exit 0
fi

ANSWER_TEXT=$(jq -c . "$ANSWER_FILE" | tr '[:upper:]' '[:lower:]')

# gt_chain = gt.error_chain only when gt is an object and error_chain is a list; else empty
STEPS=$(jq -r 'if (type=="object") and ((.error_chain|type)=="array") then (.error_chain|length) else -1 end' "$GT_FILE")
if [[ "$STEPS" -le 0 ]]; then
  echo '{"score": 0.0, "passed": false, "detail": "No error_chain in ground_truth"}'
  exit 0
fi

found=0
i=0
while [[ $i -lt $STEPS ]]; do
  # only string steps are considered; non-string steps are skipped (not counted)
  is_str=$(jq -r --argjson i "$i" '.error_chain[$i] | type=="string"' "$GT_FILE")
  if [[ "$is_str" == "true" ]]; then
    kws=$(jq -r --argjson i "$i" '
      .error_chain[$i] | ascii_downcase | splits("[ \t\n\r\f]+") | select(length>5)' "$GT_FILE" | head -n 3)
    # nextjs: only count when keyword list is non-empty AND all present
    if [[ -n "$kws" ]]; then
      step_ok=1
      while IFS= read -r kw; do
        [[ -z "$kw" ]] && continue
        if ! printf '%s' "$ANSWER_TEXT" | grep -qF -- "$kw"; then step_ok=0; break; fi
      done <<<"$kws"
      [[ $step_ok -eq 1 ]] && found=$((found+1))
    fi
  fi
  i=$((i+1))
done

# score = found/STEPS ; output round(score,2) ; pass at unrounded >=0.5 (2*found>=STEPS)
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
if [[ $((2*found)) -ge $STEPS ]]; then passed=true; else passed=false; fi

printf '{"score": %s, "passed": %s, "detail": "Matched %s/%s error chain steps"}\n' "$score" "$passed" "$found" "$STEPS"
