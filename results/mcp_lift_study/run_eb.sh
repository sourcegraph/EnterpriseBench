#!/bin/bash
# MCP-lift study (EnterpriseBench-323) — EB side: 10 tasks x 2 conditions.
# Cond A: claude-sonnet-4-6 + Sourcegraph MCP (mcp_only)
# Cond B: claude-fable-5, no MCP (baseline)
# 4 waves of 5 parallel runs, accounts 1-5, mode-suffixed image tags.
set -u
cd "$(dirname "$0")/../.."

TASKS=(
  "ansible-galaxy-tar-regression-prove-001 benchmarks/incident_response/ansible-galaxy-tar-regression-prove-001/task.toml"
  "refactor-orch-001 benchmarks/technical_debt/refactor-orchestration-001/task.toml"
  "monorepo-boundary-pnpm-lockfile-005 benchmarks/feature_delivery/monorepo-boundary-005/task.toml"
  "dead-code-003 benchmarks/technical_debt/dead-code-003/task.toml"
  "support-map-grafana-import-003 benchmarks/customer_escalation/support-mapping-003/task.toml"
  "api-contract-grpc-metadata-001 benchmarks/dependency_management/api-contract-001/task.toml"
  "api-contract-grpc-transport-003 benchmarks/dependency_management/api-contract-003/task.toml"
  "refactor-orchestration-tri-tokio-001 benchmarks/technical_debt/refactor-orchestration-tri-tokio-001/task.toml"
  "dep-traversal-010 benchmarks/dependency_management/dep-traversal-010/task.toml"
  "config-drift-tri-kustomize-001 benchmarks/platform_engineering/config-drift-tri-kustomize-001/task.toml"
)

AGENT_A='claude --dangerously-skip-permissions --max-turns 50 --verbose --output-format stream-json --model claude-sonnet-4-6 -p'
AGENT_B='claude --dangerously-skip-permissions --max-turns 50 --verbose --output-format stream-json --model claude-fable-5 -p'

OUT=results/mcp_lift_study/eb
LOGS=results/mcp_lift_study/logs
mkdir -p "$OUT" "$LOGS"

run_one() {  # task_id toml cond mode agent account
  local id=$1 toml=$2 cond=$3 mode=$4 agent=$5 acct=$6
  if [ -f "$OUT/$id/$cond/results.json" ]; then
    echo "[skip] $id/$cond already has results.json"
    return 0
  fi
  echo "[start $(date +%H:%M:%S)] $id $cond (mode=$mode acct=$acct)"
  python3 scripts/orchestration/run_task.py "$toml" \
    --mode "$mode" --account "$acct" \
    --agent "$agent" \
    --output-dir "$OUT/$id/$cond" \
    --min-disk-gb 5 \
    > "$LOGS/${id}_${cond}.log" 2>&1
  echo "[done  $(date +%H:%M:%S)] $id $cond exit=$?"
}

wave() {  # cond mode agent start_idx
  local cond=$1 mode=$2 agent=$3 start=$4
  local acct=1
  for i in 0 1 2 3 4; do
    local entry="${TASKS[$((start + i))]}"
    local id=${entry%% *}
    local toml=${entry#* }
    run_one "$id" "$toml" "$cond" "$mode" "$agent" "$acct" &
    acct=$((acct + 1))
  done
  wait
}

echo "=== Wave 1: tasks 1-5 condA (sonnet-4-6 + MCP) ==="
wave condA mcp_only "$AGENT_A" 0
echo "=== Wave 2: tasks 6-10 condA ==="
wave condA mcp_only "$AGENT_A" 5
echo "=== Wave 3: tasks 1-5 condB (fable-5, baseline) ==="
wave condB baseline "$AGENT_B" 0
echo "=== Wave 4: tasks 6-10 condB ==="
wave condB baseline "$AGENT_B" 5
echo "=== EB runs complete $(date) ==="
