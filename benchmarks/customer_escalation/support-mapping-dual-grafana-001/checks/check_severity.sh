#!/usr/bin/env bash
# check_severity.sh — verify agent correctly assessed severity
# Reads agent output from $WORKSPACE/agent_output/answer.json
# Scores the severity level against the task's expected_severity (distance-based).
# Severity-only, matching this task's expected_solution criteria and the
# support-mapping family standard (no task-specific mechanism gate).
set -euo pipefail

export ANSWER_FILE="${WORKSPACE:-/workspace}/agent_output/answer.json"
export GT_FILE="${TASK_DIR:-/task}/ground_truth.json"

if [[ ! -f "$ANSWER_FILE" ]]; then
    echo '{"score": 0.0, "passed": false, "detail": "No answer.json found"}'
    exit 1
fi

if [[ ! -f "$GT_FILE" ]]; then
    echo '{"score": 0.0, "passed": false, "detail": "No ground_truth.json found"}'
    exit 1
fi

expected_severity=$(jq -r '(.expected_severity // "") | ascii_downcase' "$GT_FILE")

if [[ -z "$expected_severity" ]]; then
    jq -cn '{score: 0.0, passed: false, detail: "No expected severity in ground truth"}'
    exit 0
fi

# sev = answer.get('severity', answer.get('impact', answer.get('priority','')))
# dict -> level/severity/rating ; str -> itself ; else -> str(sev)
agent_severity=$(jq -r '
  def pick: if has("severity") then .severity
            elif has("impact") then .impact
            elif has("priority") then .priority
            else "" end;
  pick as $sev
  | (if ($sev|type)=="object" then ($sev.level // $sev.severity // $sev.rating // "")
     elif ($sev|type)=="string" then $sev
     elif ($sev|type)=="boolean" then (if $sev then "True" else "False" end)
     elif ($sev|type)=="null" then "None"
     else ($sev|tostring) end) | ascii_downcase
' "$ANSWER_FILE")

if [[ -z "${agent_severity//[[:space:]]/}" ]]; then
    jq -cn '{score: 0.0, passed: false, detail: "Agent provided no severity assessment"}'
    exit 0
fi

level_idx() {
  case "$1" in
    low) echo 0;; medium) echo 1;; high) echo 2;; critical) echo 3;; *) echo -1;;
  esac
}
expected_idx=$(level_idx "$expected_severity")
agent_idx=-1
for level in low medium high critical; do
  if grep -qF -- "$level" <<<"$agent_severity"; then
    agent_idx=$(level_idx "$level")
    break
  fi
done

if [[ "$expected_idx" -lt 0 || "$agent_idx" -lt 0 ]]; then
  if grep -qF -- "$expected_severity" <<<"$agent_severity"; then score_str="1.0"; else score_str="0.0"; fi
else
  distance=$(( expected_idx - agent_idx )); distance=${distance#-}
  case "$distance" in
    0) score_str="1.0";; 1) score_str="0.6";; 2) score_str="0.2";; *) score_str="0.0";;
  esac
fi

passed=$(jq -n --argjson s "$score_str" 'if $s >= 0.3 then true else false end')
detail="Expected: ${expected_severity}, Agent: ${agent_severity} (score=${score_str})"
jq -cn --argjson s "$score_str" --argjson p "$passed" --arg d "$detail" \
  '{score: $s, passed: $p, detail: $d}'
