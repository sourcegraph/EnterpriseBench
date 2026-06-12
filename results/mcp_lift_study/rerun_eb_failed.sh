#!/bin/bash
# MCP-lift study — rerun the 4 EB runs invalidated by 429 rate limits / timeout.
set -u
cd "$(dirname "$0")/../.."

AGENT_A='claude --dangerously-skip-permissions --max-turns 50 --verbose --output-format stream-json --model claude-sonnet-4-6 -p'
AGENT_B='claude --dangerously-skip-permissions --max-turns 50 --verbose --output-format stream-json --model claude-fable-5 -p'
OUT=results/mcp_lift_study/eb
LOGS=results/mcp_lift_study/logs

run_one() {
  local id=$1 toml=$2 cond=$3 mode=$4 agent=$5 acct=$6
  [ -f "$OUT/$id/$cond/results.json" ] && { echo "[skip] $id/$cond"; return 0; }
  echo "[start $(date +%H:%M:%S)] $id $cond (mode=$mode acct=$acct)"
  python3 scripts/orchestration/run_task.py "$toml" \
    --mode "$mode" --account "$acct" --agent "$agent" \
    --output-dir "$OUT/$id/$cond" --min-disk-gb 5 \
    > "$LOGS/${id}_${cond}_rerun.log" 2>&1
  echo "[done  $(date +%H:%M:%S)] $id $cond exit=$?"
}

run_one ansible-galaxy-tar-regression-prove-001 benchmarks/incident_response/ansible-galaxy-tar-regression-prove-001/task.toml condA mcp_only "$AGENT_A" 1 &
run_one refactor-orch-001 benchmarks/technical_debt/refactor-orchestration-001/task.toml condB baseline "$AGENT_B" 3 &
run_one api-contract-grpc-transport-003 benchmarks/dependency_management/api-contract-003/task.toml condA mcp_only "$AGENT_A" 4 &
run_one api-contract-grpc-transport-003 benchmarks/dependency_management/api-contract-003/task.toml condB baseline "$AGENT_B" 5 &
wait
echo "=== reruns complete $(date) ==="
