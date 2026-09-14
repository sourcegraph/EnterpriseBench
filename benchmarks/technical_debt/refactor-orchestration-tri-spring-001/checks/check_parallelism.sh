#!/usr/bin/env bash
# Checkpoint 3: Verify agent correctly identifies parallelizable steps
set -euo pipefail

ANSWER="${WORKSPACE:-/workspace}/REFACTOR_PLAN.md"
GT="${TASK_DIR:-$(dirname "$(dirname "$0")")}/ground_truth.json"

if [[ ! -f "$ANSWER" ]]; then
  printf '{"score": 0.0, "passed": false, "reason": "REFACTOR_PLAN.md not found"}\n'
  exit 0
fi

if [[ ! -f "$GT" ]]; then
  printf '{"score": 0.0, "passed": false, "reason": "ground_truth.json not found"}\n'
  exit 0
fi

export ANSWER_FILE="$ANSWER"
export GT_FILE="$GT"

python3 -c "
import json, os

with open(os.environ['GT_FILE']) as f:
    gt = json.load(f)
with open(os.environ['ANSWER_FILE']) as f:
    answer_text = f.read().lower()

parallel_steps = gt.get('parallelizable_steps', [])

# Check if agent addresses parallelism at all
mentions_parallel = any(term in answer_text for term in ['parallel', 'concurrent', 'simultaneous', 'independent'])

# 'depends on' is deliberately excluded here — it's a phrase from the prompt's
# own output-format instructions ('Dependency graph (who depends on whom)'),
# not evidence the agent investigated anything.
no_parallel_stmt = any(term in answer_text for term in [
    'no parallelizable', 'not parallelizable', 'sequential', 'serial',
    'no parallel', 'fully sequential', 'strictly linear', 'strictly sequential',
    'cannot be parallelized', 'cannot run in parallel',
])
# Repo-specific breaking-change grounding: only a plan that actually cites the
# concrete artifacts from the repos (not just the prompt's generic framing)
# earns full credit for this checkpoint.
breaking_change_grounded = any(term in answer_text for term in [
    'partition assignment', 'partition-assignment', 'listener.concurrency',
    'kafkaproperties.listener.concurrency', 'concurrentmessagelistenercontainer',
])

if not parallel_steps:
    # No parallel steps expected — agent should note sequential dependency
    if no_parallel_stmt and breaking_change_grounded:
        score = 1.0
        detail = 'Correctly identified no parallelizable steps (sequential chain), grounded in repo-specific breaking changes'
    elif no_parallel_stmt:
        score = 0.6
        detail = 'Identified sequential chain but did not ground breaking changes in repo-specific artifacts'
    elif mentions_parallel:
        score = 0.3
        detail = 'Mentions parallelism but chain is fully sequential'
    else:
        score = 0.5
        detail = 'Did not explicitly address parallelism'
    passed = score >= 0.5
else:
    if mentions_parallel:
        score = 0.8
        detail = 'Agent addresses parallelism opportunities'
    else:
        score = 0.2
        detail = 'Agent did not identify parallelism opportunities'
    passed = score >= 0.5

print(json.dumps({'score': round(score, 2), 'passed': passed, 'reason': detail}))
"
