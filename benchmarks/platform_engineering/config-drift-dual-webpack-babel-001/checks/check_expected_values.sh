#!/usr/bin/env bash
# check_expected_values.sh — verify the agent's answer mentions the correct
# webpack/babel ordering semantics (semantic keyword match against
# ground_truth.expected_config_terms).
# Reimplemented in bash+jq+grep (no python3 in container); scoring (found/total,
# round(score,2) in output, pass at raw score >= 0.5) identical to the previous
# python implementation.
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

# Parse error (invalid JSON in either file) -> Parse error, exit 0.
if ! answer_text=$(jq -c '.' "$ANSWER_FILE" 2>/dev/null) || ! jq -e . "$GT_FILE" >/dev/null 2>&1; then
    echo '{"score": 0.0, "passed": false, "detail": "Parse error"}'
    exit 0
fi
answer_text=$(printf '%s' "$answer_text" | tr '[:upper:]' '[:lower:]')

# terms must be a non-empty list (gt a dict with list-valued expected_config_terms).
if [[ "$(jq -r 'if (type=="object") and (.expected_config_terms | type == "array") then "ok" else "no" end' "$GT_FILE")" != "ok" ]] \
   || [[ "$(jq -r '.expected_config_terms | length' "$GT_FILE")" -eq 0 ]]; then
    echo '{"score": 0.0, "passed": false, "detail": "No expected_config_terms in ground_truth"}'
    exit 0
fi

total=$(jq -r '.expected_config_terms | length' "$GT_FILE")
found=0
for ((i = 0; i < total; i++)); do
    # only string terms count; t.lower() in answer_text (full lowercased dump).
    if [[ "$(jq -r ".expected_config_terms[$i] | type" "$GT_FILE")" != "string" ]]; then continue; fi
    term=$(jq -r ".expected_config_terms[$i]" "$GT_FILE" | tr '[:upper:]' '[:lower:]')
    if printf '%s' "$answer_text" | grep -qF -- "$term"; then
        found=$((found + 1))
    fi
done

# score = found/total; output round(score,2); passed = raw score >= 0.5.
score=$(awk "BEGIN {printf \"%.2f\", $found/$total}")
score=${score%0}; score=${score%.}; case "$score" in *.*) ;; *) score="$score.0";; esac
if awk "BEGIN {exit !(($found/$total) >= 0.5)}"; then passed=true; else passed=false; fi

printf '{"score": %s, "passed": %s, "detail": "Mentioned %d/%d expected config terms"}\n' \
    "$score" "$passed" "$found" "$total"
