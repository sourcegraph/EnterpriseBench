#!/usr/bin/env bash
# check_dependency_trace.sh — verify the agent's answer mentions the JSONField
# dependency-chain concepts (semantic keyword match against
# ground_truth.dependency_trace_terms).
#
# Reimplemented in bash+jq+grep (no python3 in container — the task image ships
# bash/grep/jq but not python3, so the previous `python3 -c` body exited 127).
# Scoring is identical: a term counts when its lowercased value is a substring of
# the lowercased json.dumps(answer); score = matched-string-terms / total-terms.
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

# Malformed JSON in either file -> generic Parse error, exit 0.
if ! jq -e . "$ANSWER_FILE" >/dev/null 2>&1 || ! jq -e . "$GT_FILE" >/dev/null 2>&1; then
    echo '{"score": 0.0, "passed": false, "detail": "Parse error"}'
    exit 0
fi

# json.dumps(answer).lower() — python default separators ", " / ": " (with spaces).
answer_text=$(jq -c . "$ANSWER_FILE" | sed 's/,/, /g; s/:/: /g' | tr '[:upper:]' '[:lower:]')

# dependency_trace_terms must be a list; otherwise treated as empty.
if [[ "$(jq -r 'if type=="object" and (.dependency_trace_terms|type)=="array" then "list" else "x" end' "$GT_FILE")" != "list" ]]; then
    echo '{"score": 0.0, "passed": false, "detail": "No dependency_trace_terms in ground_truth"}'
    exit 0
fi

# Total = every list entry (including non-strings); matched = string entries whose
# lowercase appears in answer_text.
total=$(jq '.dependency_trace_terms | length' "$GT_FILE")
if [[ "$total" -eq 0 ]]; then
    echo '{"score": 0.0, "passed": false, "detail": "No dependency_trace_terms in ground_truth"}'
    exit 0
fi

# Only string terms are eligible to match (python isinstance(t,str)).
mapfile -t terms < <(jq -r '.dependency_trace_terms[] | select(type=="string") | ascii_downcase' "$GT_FILE")

found=0
for t in "${terms[@]}"; do
    if printf '%s' "$answer_text" | grep -qF -- "$t"; then
        found=$((found + 1))
    fi
done

score=$(jq -n --argjson f "$found" --argjson t "$total" '
  (($f / $t * 100) | round) / 100
  | if . == (. | floor) then "\(.).0" else (. | tostring) end' | tr -d '"')
passed=$(jq -n --argjson f "$found" --argjson t "$total" 'if ($f / $t) >= 0.5 then true else false end')
printf '{"score": %s, "passed": %s, "detail": "Mentioned %s/%s dependency-trace concept terms"}\n' \
    "$score" "$passed" "$found" "$total"
