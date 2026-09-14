#!/usr/bin/env bash
# check_werkzeug_bridge.sh — verify the agent's answer mentions Werkzeug's
# Local/contextvars bridge concepts (semantic keyword match against
# ground_truth.werkzeug_bridge_terms).
# bash+jq+grep (no python3 in container). Scoring semantics identical to the
# previous python implementation.
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

# Parse-error branch: if either file is not valid JSON, emit Parse error.
if ! jq -e . "$ANSWER_FILE" >/dev/null 2>&1 || ! jq -e . "$GT_FILE" >/dev/null 2>&1; then
    echo '{"score": 0.0, "passed": false, "detail": "Parse error"}'
    exit 0
fi

# answer_text = json.dumps(answer).lower()
answer_text=$(jq -c . "$ANSWER_FILE" | tr '[:upper:]' '[:lower:]')

# terms = gt.get('werkzeug_bridge_terms', []) if gt is a dict else []
gt_type=$(jq -r 'type' "$GT_FILE")
if [[ "$gt_type" != "object" ]]; then
    echo '{"score": 0.0, "passed": false, "detail": "No werkzeug_bridge_terms in ground_truth"}'
    exit 0
fi
terms_type=$(jq -r '.werkzeug_bridge_terms | type' "$GT_FILE")
n_terms=$(jq -r 'if (.werkzeug_bridge_terms|type)=="array" then (.werkzeug_bridge_terms|length) else 0 end' "$GT_FILE")
if [[ "$terms_type" != "array" || "$n_terms" -eq 0 ]]; then
    echo '{"score": 0.0, "passed": false, "detail": "No werkzeug_bridge_terms in ground_truth"}'
    exit 0
fi

# found = sum(1 for t in terms if isinstance(t,str) and t.lower() in answer_text)
found=0
total=$(jq -r '.werkzeug_bridge_terms | length' "$GT_FILE")
for ((i = 0; i < total; i++)); do
    t_type=$(jq -r ".werkzeug_bridge_terms[$i] | type" "$GT_FILE")
    [[ "$t_type" == "string" ]] || continue
    term=$(jq -r ".werkzeug_bridge_terms[$i]" "$GT_FILE" | tr '[:upper:]' '[:lower:]')
    if printf '%s' "$answer_text" | grep -qF -- "$term"; then
        found=$((found + 1))
    fi
done

# round(found/total, 2) with python's round-half-to-even (banker's rounding);
# python json.dumps keeps a trailing .0 on integral floats.
score=$(jq -n --argjson f "$found" --argjson t "$total" '
  (($f / $t) * 100) as $v | ($v | floor) as $fl | ($v - $fl) as $fr |
  ((if $fr < 0.5 then $fl elif $fr > 0.5 then $fl + 1
    else (if ($fl % 2) == 0 then $fl else $fl + 1 end) end) / 100) | tostring' | tr -d '"')
case "$score" in *.*) ;; *) score="${score}.0";; esac
passed=$(jq -n --argjson f "$found" --argjson t "$total" 'if ($f / $t) >= 0.5 then true else false end')
printf '{"score": %s, "passed": %s, "detail": "Mentioned %s/%s Werkzeug bridge concept terms"}\n' \
    "$score" "$passed" "$found" "$total"
