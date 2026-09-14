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

mentions_parallel = any(term in answer_text for term in ['parallel', 'concurrent', 'simultaneous', 'independent'])
mentions_sequential = any(term in answer_text for term in ['sequential', 'serial', 'no parallel', 'cannot.*parallel', 'depends on'])

# Repo-specific evidence gate: the *reason* the chain is non-parallelizable is
# that axum depends directly on both tokio and hyper's runtime/server APIs --
# not just that tokio precedes hyper precedes axum. Require the plan to name
# the actual downstream API surface driving that (found by reading hyper's
# and axum's source), not just restate the upstream change description
# (runtime builder / task scheduler) that's already given in the prompt.
evidence_terms = ['server::bind', 'connection handler', 'tokio::spawn', 'server builder']
has_evidence = any(term in answer_text for term in evidence_terms)

if not parallel_steps:
    if mentions_sequential and has_evidence:
        score = 1.0
        detail = 'Correctly identified no parallelizable steps, grounded in the specific API surface'
    elif mentions_sequential:
        score = 0.5
        detail = 'Identified sequential ordering but did not ground it in a specific API surface'
    elif mentions_parallel:
        score = 0.3
        detail = 'Mentions parallelism but chain is fully sequential'
    else:
        score = 0.4
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
